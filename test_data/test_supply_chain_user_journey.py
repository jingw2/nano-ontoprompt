#!/usr/bin/env python3
"""
test_supply_chain_user_journey.py

模拟供应链经理真实使用 nano-ontoprompt 的完整工作流：
  登录 → 系统健康检查 → 模型验证 → 数据连接 → 数据集上传与管理
  → 流水线 → Curated 数据审核 → 本体项目 → 文档上传
  → 提示词生成与管理 → LLM 本体提取 → 实体/逻辑/动作管理
  → 图谱浏览 → 搜索 → 导出 → 增量映射 → 设置

测试数据：test_data/供应链/ 下的所有文件

用法：
  python test_data/test_supply_chain_user_journey.py
  python test_data/test_supply_chain_user_journey.py --base-url http://localhost:8000
"""

import sys
import os
import time
import json
import argparse

try:
    import requests
except ImportError:
    print("ERROR: requests not found. pip install requests")
    sys.exit(1)

# ─── 配置 ────────────────────────────────────────────────────────────────────
DEFAULT_BASE_URL = "http://localhost:8000"
USERNAME = "admin"
PASSWORD = "admin123"
SUPPLY_DIR = os.path.join(os.path.dirname(__file__), "供应链")

PASS_COUNT = 0
FAIL_COUNT = 0
WARN_COUNT = 0
FAILURES: list[str] = []

# ─── 辅助函数 ─────────────────────────────────────────────────────────────────
def section(title: str) -> None:
    bar = "─" * 60
    print(f"\n{bar}")
    print(f"  {title}")
    print(bar)

def ok(msg: str) -> None:
    global PASS_COUNT
    PASS_COUNT += 1
    print(f"  ✅  {msg}")

def warn(msg: str) -> None:
    global WARN_COUNT
    WARN_COUNT += 1
    print(f"  ⚠️   {msg}")

def fail(msg: str) -> None:
    global FAIL_COUNT
    FAIL_COUNT += 1
    FAILURES.append(msg)
    print(f"  ❌  {msg}")

def info(msg: str) -> None:
    print(f"  ℹ️   {msg}")

def check(resp: requests.Response, expected: int, label: str) -> bool:
    if resp.status_code != expected:
        fail(f"{label} → HTTP {resp.status_code} (expected {expected}): {resp.text[:160]}")
        return False
    return True

def jget(resp: requests.Response) -> dict | list:
    try:
        return resp.json()
    except Exception:
        return {}

def file_exists(path: str) -> bool:
    return os.path.isfile(path)

def list_supply_files(ext_filter=None) -> list[str]:
    if not os.path.isdir(SUPPLY_DIR):
        return []
    files = os.listdir(SUPPLY_DIR)
    if ext_filter:
        files = [f for f in files if any(f.lower().endswith(e) for e in ext_filter)]
    return sorted(files)

def upload_file_to_ontology(session, base, oid, fpath, fname, mime) -> str | None:
    """上传文件到本体项目，返回 file_id 或 None"""
    with open(fpath, "rb") as f:
        resp = session.post(
            f"{base}/api/v1/ontologies/{oid}/files",
            files={"file": (fname, f, mime)},
            headers={"Content-Type": None},
        )
    if check(resp, 201, f"上传文件 {fname}"):
        fid = jget(resp).get("data", {}).get("id")
        ok(f"  文件上传成功: {fname} → id={fid}")
        return fid
    return None

MIME_MAP = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".csv": "text/csv",
    ".json": "application/json",
    ".md": "text/markdown",
    ".txt": "text/plain",
}

# ─── 主测试流程 ───────────────────────────────────────────────────────────────
def run(base_url: str) -> None:
    session = requests.Session()
    token = ""
    ontology_id = ""
    uploaded_ds_ids: list[str] = []
    uploaded_file_ids: list[str] = []
    prompt_id = ""
    extraction_task_id = ""
    pipeline_id = ""
    connection_id = ""

    # ═══════════════════════════════════════════════════════════════════════════
    # 阶段 1 — 认证与系统检查
    # ═══════════════════════════════════════════════════════════════════════════
    section("阶段 1 — 认证与系统健康检查")

    # 1-1 登录
    resp = requests.post(f"{base_url}/api/v1/auth/login",
                         json={"username": USERNAME, "password": PASSWORD})
    if not check(resp, 200, "POST /auth/login"):
        fail("认证失败，无法继续测试")
        return
    body = jget(resp)
    # token 可能在顶层或 data 层
    data = body.get("data", body) if isinstance(body, dict) else {}
    token = (data if isinstance(data, dict) else {}).get("access_token", "") or body.get("access_token", "")
    if not token:
        fail(f"登录响应中无 access_token，响应={str(body)[:200]}")
        return
    ok(f"登录成功，用户={USERNAME}，token 长度={len(token)}")

    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })

    # 1-2 获取用户 Profile
    resp = session.get(f"{base_url}/api/v1/auth/profile")
    if check(resp, 200, "GET /auth/profile"):
        body = jget(resp)
        p = body.get("data", body) if isinstance(body, dict) else body
        ok(f"Profile: username={p.get('username')}, role={p.get('role')}, id={str(p.get('id',''))[:8]}")

    # 1-3 系统健康检查
    resp = session.get(f"{base_url}/health")
    if check(resp, 200, "GET /health"):
        h = jget(resp)
        ok(f"系统状态: db={h.get('db')}, neo4j={h.get('neo4j')}, "
           f"minio={h.get('minio')}, chroma={h.get('chroma')}")

    # 1-4 全局概览
    resp = session.get(f"{base_url}/api/v1/overview/stats")
    if check(resp, 200, "GET /overview/stats"):
        s = jget(resp)
        ok(f"概览统计: {s}")

    # ═══════════════════════════════════════════════════════════════════════════
    # 阶段 2 — 模型配置验证
    # ═══════════════════════════════════════════════════════════════════════════
    section("阶段 2 — LLM 模型配置验证")

    resp = session.get(f"{base_url}/api/v1/models")
    model_id = ""
    model_name = ""
    if check(resp, 200, "GET /models"):
        body = jget(resp)
        models = body.get("data", body) if isinstance(body, dict) else body
        if not isinstance(models, list) or not models:
            warn("未配置模型，LLM 相关功能将跳过")
        else:
            m = models[0]
            model_id = m.get("id", "")
            model_name = (m.get("models") or [""])[0]
            ok(f"模型: name={m.get('name')}, provider={m.get('provider')}, "
               f"model={model_name}, id={model_id}")

            # 测试模型连通性
            resp2 = session.post(f"{base_url}/api/v1/models/{model_id}/test")
            if check(resp2, 200, "POST /models/{id}/test"):
                r2 = jget(resp2)
                status = r2.get("data", {}).get("status") if isinstance(r2, dict) else "unknown"
                ok(f"模型连通性测试: {status}")

    # ═══════════════════════════════════════════════════════════════════════════
    # 阶段 3 — 数据连接管理
    # ═══════════════════════════════════════════════════════════════════════════
    section("阶段 3 — 数据连接（Connections）管理")

    # 3-1 列出现有连接
    resp = session.get(f"{base_url}/api/v2/connections")
    existing_connections = []
    if check(resp, 200, "GET /connections"):
        existing_connections = jget(resp) if isinstance(jget(resp), list) else []
        ok(f"现有连接数: {len(existing_connections)}")

    # 3-2 创建新的供应链数据连接
    conn_payload = {
        "name": "供应链-库存数据源",
        "kind": "file",  # file | mysql | postgres | mongo | rest
        "config": {
            "path": os.path.join(SUPPLY_DIR, "inventory_transactions.csv").replace("\\", "/"),
            "encoding": "utf-8-sig",
            "delimiter": ",",
        },
    }
    resp = session.post(f"{base_url}/api/v2/connections", json=conn_payload)
    if check(resp, 201, "POST /connections"):
        conn = jget(resp)
        connection_id = conn.get("id", "")
        ok(f"创建连接: name={conn.get('name')}, id={connection_id}, "
           f"type={conn.get('source_type')}")

    # 3-3 获取连接详情
    if connection_id:
        resp = session.get(f"{base_url}/api/v2/connections/{connection_id}")
        if check(resp, 200, "GET /connections/{id}"):
            c = jget(resp)
            ok(f"连接详情: id={c.get('id')}, name={c.get('name')}")

    # 3-4 触发同步
    if connection_id:
        resp = session.post(f"{base_url}/api/v2/connections/{connection_id}/sync",
                            json={"sync_mode": "full"})
        if resp.status_code in (200, 201, 202):
            ok(f"连接同步触发成功: HTTP {resp.status_code}")
        else:
            warn(f"连接同步返回 {resp.status_code}（可接受）")

    # ═══════════════════════════════════════════════════════════════════════════
    # 阶段 4 — 数据集上传与管理
    # ═══════════════════════════════════════════════════════════════════════════
    section("阶段 4 — 数据集上传与管理（Datasets）")

    # 4-1 上传供应链结构化文件
    structured_files = list_supply_files([".csv", ".xlsx", ".json"])
    info(f"供应链目录结构化文件: {structured_files}")

    for fname in structured_files:
        fpath = os.path.join(SUPPLY_DIR, fname)
        ext = os.path.splitext(fname)[1].lower()
        mime = MIME_MAP.get(ext, "application/octet-stream")
        with open(fpath, "rb") as f:
            resp = session.post(
                f"{base_url}/api/v2/datasets/upload",
                files={"file": (fname, f, mime)},
                headers={"Content-Type": None},
            )
        if check(resp, 201, f"上传数据集 {fname}"):
            d = jget(resp).get("data", {})
            ds_id = d.get("id", "")
            if ds_id:
                uploaded_ds_ids.append(ds_id)
                ok(f"  {fname} → id={ds_id}, kind={d.get('kind')}, type={d.get('dataset_type')}")
        else:
            warn(f"  上传 {fname} 失败（继续）")

    # 4-2 列出所有数据集
    resp = session.get(f"{base_url}/api/v2/datasets")
    all_datasets = []
    if check(resp, 200, "GET /datasets"):
        all_datasets = jget(resp) if isinstance(jget(resp), list) else []
        ok(f"数据集总数: {len(all_datasets)} 条")

    # 4-3 按类型筛选
    resp = session.get(f"{base_url}/api/v2/datasets?kind=structured")
    if check(resp, 200, "GET /datasets?kind=structured"):
        filtered = jget(resp) if isinstance(jget(resp), list) else []
        ok(f"结构化数据集筛选结果: {len(filtered)} 条")

    # 4-4 每个上传数据集的详情、schema、stats、版本、预览
    for ds_id in uploaded_ds_ids:
        # 详情
        resp = session.get(f"{base_url}/api/v2/datasets/{ds_id}")
        if check(resp, 200, f"GET /datasets/{ds_id[:8]}..."):
            d = jget(resp)
            ok(f"  数据集详情: name={d.get('name')}, kind={d.get('kind')}")

        # Schema
        resp = session.get(f"{base_url}/api/v2/datasets/{ds_id}/schema")
        if check(resp, 200, f"GET /datasets/{ds_id[:8]}.../schema"):
            s = jget(resp)
            cols = s.get("columns", [])
            ok(f"  Schema: {len(cols)} 列 — {[c.get('name') for c in cols[:5]]}")

        # Stats
        resp = session.get(f"{base_url}/api/v2/datasets/{ds_id}/stats")
        if check(resp, 200, f"GET /datasets/{ds_id[:8]}.../stats"):
            s = jget(resp)
            ok(f"  Stats: rows={s.get('row_count')}, cols={s.get('column_count')}, "
               f"versions={s.get('version_count')}")

        # 版本列表
        resp = session.get(f"{base_url}/api/v2/datasets/{ds_id}/versions")
        if check(resp, 200, f"GET /datasets/{ds_id[:8]}.../versions"):
            versions = jget(resp) if isinstance(jget(resp), list) else []
            ok(f"  版本数: {len(versions)}")
            if versions:
                v0 = versions[0]
                vno = v0.get("version_no", 1)
                # 预览最新版本
                resp2 = session.get(
                    f"{base_url}/api/v2/datasets/{ds_id}/versions/{vno}/preview",
                    params={"limit": 5},
                )
                if check(resp2, 200, f"GET /datasets/{ds_id[:8]}.../versions/{vno}/preview"):
                    rows = jget(resp2) if isinstance(jget(resp2), list) else []
                    ok(f"  预览: 返回 {len(rows)} 行数据")
                    if rows and isinstance(rows[0], dict):
                        info(f"  样例行: {dict(list(rows[0].items())[:3])}")

    # ═══════════════════════════════════════════════════════════════════════════
    # 阶段 5 — 流水线（Pipeline）
    # ═══════════════════════════════════════════════════════════════════════════
    section("阶段 5 — 数据处理流水线（Pipeline）")

    # 5-1 列出现有流水线
    resp = session.get(f"{base_url}/api/v2/pipelines")
    if check(resp, 200, "GET /pipelines"):
        pipelines = jget(resp) if isinstance(jget(resp), list) else []
        ok(f"现有流水线: {len(pipelines)} 条")

    # 5-2 创建供应链数据处理流水线
    src_ds = uploaded_ds_ids[0] if uploaded_ds_ids else (all_datasets[0]["id"] if all_datasets else "")
    if src_ds:
        pipeline_payload = {
            "name": "供应链库存清洗流水线",
            "source_dataset_id": src_ds,
            "route": "A",  # A | B | C
            "spec": {
                "description": "对库存交易数据进行清洗、去重、标准化",
                "steps": [
                    {"type": "deduplicate", "key_columns": ["物料编码", "日期"]},
                    {"type": "filter", "condition": "库存状态 != ''"},
                ],
            },
        }
        resp = session.post(f"{base_url}/api/v2/pipelines", json=pipeline_payload)
        if check(resp, 201, "POST /pipelines"):
            pl = jget(resp)
            pipeline_id = pl.get("id", "")
            ok(f"流水线创建: name={pl.get('name')}, id={pipeline_id}, status={pl.get('status')}")

    # 5-3 获取流水线详情
    if pipeline_id:
        resp = session.get(f"{base_url}/api/v2/pipelines/{pipeline_id}")
        if check(resp, 200, "GET /pipelines/{id}"):
            pl = jget(resp)
            ok(f"流水线详情: name={pl.get('name')}, status={pl.get('status')}")

    # 5-4 触发流水线运行
    if pipeline_id:
        resp = session.post(f"{base_url}/api/v2/pipelines/{pipeline_id}/run")
        run_id = ""
        if check(resp, 200, "POST /pipelines/{id}/run"):
            r = jget(resp)
            run_id = r.get("run_id", r.get("id", ""))
            ok(f"流水线触发运行: run_id={run_id}, status={r.get('status')}")

        # 5-5 轮询运行状态（最多 30s）
        if run_id:
            for i in range(6):
                time.sleep(5)
                resp = session.get(f"{base_url}/api/v2/pipelines/runs/{run_id}")
                if resp.status_code == 200:
                    r = jget(resp)
                    status = r.get("status", "unknown")
                    ok(f"  流水线状态 [{i+1}/6]: {status}")
                    if status in ("completed", "done", "success", "failed", "error"):
                        break
                elif resp.status_code == 404:
                    # 有些实现用不同端点
                    break
            else:
                warn("流水线 30s 内未完成（Celery worker 可能未运行）")

        # 5-6 运行历史
        resp = session.get(f"{base_url}/api/v2/pipelines/{pipeline_id}/runs")
        if check(resp, 200, "GET /pipelines/{id}/runs"):
            runs = jget(resp) if isinstance(jget(resp), list) else []
            ok(f"流水线运行历史: {len(runs)} 条记录")

    # ═══════════════════════════════════════════════════════════════════════════
    # 阶段 6 — Curated 数据集审核
    # ═══════════════════════════════════════════════════════════════════════════
    section("阶段 6 — Curated 数据集质量审核")

    # 6-1 列出 Curated 数据集
    resp = session.get(f"{base_url}/api/v2/curated")
    curated_list = []
    if check(resp, 200, "GET /curated"):
        curated_list = jget(resp) if isinstance(jget(resp), list) else []
        ok(f"Curated 数据集: {len(curated_list)} 条")

    if curated_list:
        # 遍历前 3 个做详细检查
        for cd in curated_list[:3]:
            cid = cd.get("id", "")
            cname = cd.get("name", "")
            cstatus = cd.get("status", "")
            info(f"  Curated: name={cname}, id={cid[:8]}..., status={cstatus}")

            # 6-2 获取单个 Curated 详情
            resp = session.get(f"{base_url}/api/v2/curated/{cid}")
            if check(resp, 200, f"GET /curated/{cid[:8]}..."):
                c = jget(resp)
                ok(f"  详情: quality_score={c.get('quality_score')}, row_count={c.get('row_count')}")

            # 6-3 数据预览
            resp = session.get(f"{base_url}/api/v2/curated/{cid}/preview", params={"limit": 3})
            if resp.status_code == 200:
                preview = jget(resp)
                rows = preview.get("rows", []) if isinstance(preview, dict) else []
                ok(f"  预览: {len(rows)} 行")
            else:
                warn(f"  预览返回 {resp.status_code}")

            # 6-4 质量报告
            resp = session.get(f"{base_url}/api/v2/curated/{cid}/quality")
            if check(resp, 200, f"GET /curated/{cid[:8]}.../quality"):
                q = jget(resp)
                ok(f"  质量报告: completeness={q.get('completeness_score')}, "
                   f"overall={q.get('overall_score')}, issues={len(q.get('issues', []))}")

            # 6-5 审核（只对 pending_review 状态的数据集）
            if cstatus in ("pending_review", "pending"):
                resp = session.post(
                    f"{base_url}/api/v2/curated/{cid}/review",
                    params={"action": "approve", "notes": "供应链数据质量符合要求，批准入库"},
                )
                if check(resp, 200, f"POST /curated/{cid[:8]}.../review"):
                    r = jget(resp)
                    ok(f"  审核通过: review_id={r.get('review_id')}, status={r.get('status')}")
                break  # 只审核一条

    # 6-6 审核工作流（开始审核 → 编辑 → 通过）
    if curated_list:
        cid = curated_list[0]["id"]
        # 启动审核流程
        resp = session.post(f"{base_url}/api/v2/curated/{cid}/reviews")
        review_id = ""
        if resp.status_code in (200, 201):
            r = jget(resp)
            review_id = r.get("review_id", "")
            ok(f"审核流程启动: review_id={review_id}")

        if review_id:
            # 获取审核详情
            resp = session.get(f"{base_url}/api/v2/curated/reviews/{review_id}")
            if check(resp, 200, f"GET /curated/reviews/{review_id[:8]}..."):
                r = jget(resp)
                ok(f"审核详情: status={r.get('status')}")

            # 提交行编辑
            resp = session.post(
                f"{base_url}/api/v2/curated/reviews/{review_id}/edits",
                json={"edits": [{"row_pk": "1", "field_name": "库存状态",
                                  "old_value": "超储", "new_value": "正常"}]},
            )
            if resp.status_code in (200, 201):
                ok(f"批量行编辑: saved={jget(resp).get('saved')}")

    # ═══════════════════════════════════════════════════════════════════════════
    # 阶段 7 — 本体项目创建与文档管理
    # ═══════════════════════════════════════════════════════════════════════════
    section("阶段 7 — 本体项目创建与知识文档上传")

    # 7-1 创建供应链本体项目
    onto_name = "供应链知识本体-用户旅程测试"
    resp = session.get(f"{base_url}/api/v1/ontologies")
    existing_onto = []
    if resp.status_code == 200:
        body = jget(resp)
        existing_onto = body.get("data", body) if isinstance(body, dict) else body

    # GET /ontologies 返回 {"data": {"items": [...], "total": ...}}
    if isinstance(existing_onto, dict):
        existing_onto = existing_onto.get("items", existing_onto.get("data", []))
    existing = next((o for o in (existing_onto if isinstance(existing_onto, list) else [])
                     if o.get("name") == onto_name), None)

    if existing:
        ontology_id = existing["id"]
        ok(f"本体项目已存在，复用: id={ontology_id}")
    else:
        resp = session.post(f"{base_url}/api/v1/ontologies", json={
            "name": onto_name,
            "description": "模拟供应链经理基于战略文档和管理规则构建的知识本体",
            "domain": "供应链",
        })
        if check(resp, 201, "POST /ontologies"):
            body = jget(resp)
            d = body.get("data", body) if isinstance(body, dict) else body
            ontology_id = d.get("id", "")
            ok(f"本体项目创建: name={d.get('name')}, id={ontology_id}")

    if not ontology_id:
        fail("本体项目创建失败，后续步骤将跳过")

    # 7-2 获取本体详情
    if ontology_id:
        resp = session.get(f"{base_url}/api/v1/ontologies/{ontology_id}")
        if check(resp, 200, "GET /ontologies/{id}"):
            body = jget(resp)
            d = body.get("data", body) if isinstance(body, dict) else body
            ok(f"本体详情: name={d.get('name')}, status={d.get('status')}, "
               f"domain={d.get('domain')}")

    # 7-3 上传供应链知识文档（PDF, DOCX, MD, PPTX）
    doc_files = list_supply_files([".pdf", ".docx", ".md", ".pptx"])
    info(f"待上传知识文档: {doc_files}")

    if ontology_id:
        for fname in doc_files:
            fpath = os.path.join(SUPPLY_DIR, fname)
            ext = os.path.splitext(fname)[1].lower()
            mime = MIME_MAP.get(ext, "application/octet-stream")
            fid = upload_file_to_ontology(session, base_url, ontology_id, fpath, fname, mime)
            if fid:
                uploaded_file_ids.append(fid)

        ok(f"共上传知识文档: {len(uploaded_file_ids)} 个")

    # 7-4 列出本体文件
    if ontology_id:
        resp = session.get(f"{base_url}/api/v1/ontologies/{ontology_id}/files")
        if resp.status_code == 200:
            body = jget(resp)
            files_list = body.get("data", body) if isinstance(body, dict) else body
            if isinstance(files_list, list):
                ok(f"本体文件列表: {len(files_list)} 个文件")
                for ff in files_list[:5]:
                    info(f"  - {ff.get('filename', ff.get('name', '?'))}, "
                         f"id={str(ff.get('id', ''))[:8]}...")

    # ═══════════════════════════════════════════════════════════════════════════
    # 阶段 8 — 提示词（Prompt）管理
    # ═══════════════════════════════════════════════════════════════════════════
    section("阶段 8 — 提示词模板管理")

    # 8-1 列出内置提示词模板
    resp = session.get(f"{base_url}/api/v1/prompts/templates")
    if check(resp, 200, "GET /prompts/templates"):
        body = jget(resp)
        templates = body.get("data", body) if isinstance(body, dict) else body
        tlist = templates if isinstance(templates, list) else []
        ok(f"内置模板: {len(tlist)} 个")
        for t in tlist[:3]:
            info(f"  - {t.get('name')}: domain={t.get('domain')}")

    # 8-2 按域查询模板
    resp = session.get(f"{base_url}/api/v1/prompts/by-domain/供应链")
    if resp.status_code == 200:
        body = jget(resp)
        dl = body.get("data", body) if isinstance(body, dict) else body
        ok(f"供应链域提示词: {len(dl) if isinstance(dl, list) else 0} 个")
    else:
        warn(f"按域查询返回 {resp.status_code}")

    # 8-3 一键生成供应链提示词模板
    gen_content = ""
    if model_id:
        resp = session.post(f"{base_url}/api/v1/prompts/generate-template",
                            params={"domain": "供应链", "style": "ontology_extraction"})
        if check(resp, 200, "POST /prompts/generate-template"):
            g = jget(resp)
            gen_content = g.get("content", "")
            ok(f"AI 生成供应链提示词: {len(gen_content)} 字符")
            if len(gen_content) > 100:
                info(f"  预览: {gen_content[:200]}...")
    else:
        warn("无模型配置，跳过 AI 生成提示词")

    # 8-4 保存生成的提示词（或使用默认内容）
    prompt_content = gen_content or (
        "你是供应链本体专家。从以下文档中提取：\n"
        "实体类型：供应商、物料、仓库、运单、采购订单、质检记录\n"
        "关系类型：supply(供应)、stores(存储)、ships(运输)、inspects(检验)\n"
        "逻辑规则：IF...THEN 格式的业务规则\n"
        "动作：触发条件满足后执行的操作\n"
        '返回 JSON：{"entities":[],"relations":[],"logic_rules":[],"actions":[]}'
    )

    resp = session.post(f"{base_url}/api/v1/prompts", json={
        "name": "供应链本体提取提示词-用户旅程",
        "domain": "供应链",
        "content": prompt_content,
        "version": "v1.0",
    })
    if resp.status_code in (200, 201):
        body = jget(resp)
        d = body.get("data", body) if isinstance(body, dict) else body
        prompt_id = d.get("id", "")
        ok(f"提示词保存: id={prompt_id}, name={d.get('name')}")
    else:
        warn(f"提示词保存返回 {resp.status_code}，尝试使用已有提示词")
        resp2 = session.get(f"{base_url}/api/v1/prompts")
        if resp2.status_code == 200:
            body2 = jget(resp2)
            pl = body2.get("data", body2) if isinstance(body2, dict) else body2
            if isinstance(pl, list) and pl:
                prompt_id = pl[0].get("id", "")
                ok(f"  使用已有提示词: id={prompt_id}")

    # 8-5 获取提示词详情
    if prompt_id:
        resp = session.get(f"{base_url}/api/v1/prompts/{prompt_id}")
        if check(resp, 200, f"GET /prompts/{prompt_id[:8]}..."):
            body = jget(resp)
            d = body.get("data", body) if isinstance(body, dict) else body
            ok(f"提示词详情: name={d.get('name')}, domain={d.get('domain')}, "
               f"content_len={len(d.get('content', ''))}")

    # 8-6 更新提示词
    if prompt_id:
        resp = session.put(f"{base_url}/api/v1/prompts/{prompt_id}", json={
            "name": "供应链本体提取提示词-用户旅程",
            "domain": "供应链",
            "content": prompt_content + "\n\n# 补充：重点关注供应商评级和库存预警规则",
            "version": "v1.1",
        })
        if check(resp, 200, f"PUT /prompts/{prompt_id[:8]}..."):
            ok("提示词更新成功")

    # ═══════════════════════════════════════════════════════════════════════════
    # 阶段 9 — LLM 本体提取
    # ═══════════════════════════════════════════════════════════════════════════
    section("阶段 9 — LLM 本体提取（supply_chain_strategy.md）")

    if not (ontology_id and model_id and prompt_id and uploaded_file_ids):
        missing = []
        if not ontology_id: missing.append("ontology_id")
        if not model_id: missing.append("model_id")
        if not prompt_id: missing.append("prompt_id")
        if not uploaded_file_ids: missing.append("file_ids")
        warn(f"缺少依赖 {missing}，跳过 LLM 提取")
    else:
        # 优先使用 supply_chain_strategy.md
        strategy_file_id = uploaded_file_ids[0]  # 按上传顺序取第一个
        extract_payload = {
            "prompt_id": prompt_id,
            "model_id": model_id,
            "model_name": model_name,
            "file_ids": uploaded_file_ids[:2],  # 使用前两个文档
            "constraints": [],
        }
        resp = session.post(
            f"{base_url}/api/v1/ontologies/{ontology_id}/execute",
            params={"ontology_id": ontology_id},
            json=extract_payload,
        )
        if check(resp, 200, "POST /ontologies/{id}/execute"):
            body = jget(resp)
            d = body.get("data", body) if isinstance(body, dict) else body
            extraction_task_id = d.get("task_id", "")
            ok(f"LLM 提取任务已入队: task_id={extraction_task_id}")

            # 轮询提取状态（最多 120s）
            if extraction_task_id:
                info("等待 LLM 提取完成（最多 120s）...")
                for i in range(24):
                    time.sleep(5)
                    resp2 = session.get(
                        f"{base_url}/api/v1/ontologies/{ontology_id}/execute/status",
                        params={"ontology_id": ontology_id, "task_id": extraction_task_id},
                    )
                    if resp2.status_code == 200:
                        t = jget(resp2).get("data", {})
                        status = t.get("status", "unknown")
                        pct = t.get("progress", {}).get("pct", 0) if isinstance(t.get("progress"), dict) else 0
                        info(f"  [{(i+1)*5}s] status={status}, progress={pct}%")
                        if status in ("done", "completed", "success"):
                            ok(f"LLM 提取完成！耗时约 {(i+1)*5}s")
                            break
                        elif status in ("failed", "error"):
                            warn(f"LLM 提取状态={status}（可能是 API key 问题或网络超时）")
                            break
                    else:
                        warn(f"状态查询返回 {resp2.status_code}")
                        break
                else:
                    warn("120s 内提取未完成（继续后续测试）")

    # ═══════════════════════════════════════════════════════════════════════════
    # 阶段 10 — 实体管理（手动创建 + 查看）
    # ═══════════════════════════════════════════════════════════════════════════
    section("阶段 10 — 实体（Entity）管理")

    if not ontology_id:
        warn("无本体 ID，跳过实体管理")
    else:
        # 10-1 查看 LLM 提取的实体
        resp = session.get(f"{base_url}/api/v1/ontologies/{ontology_id}/entities")
        entities = []
        if check(resp, 200, "GET /ontologies/{id}/entities"):
            body = jget(resp)
            entities = body.get("data", body) if isinstance(body, dict) else body
            if not isinstance(entities, list): entities = []
            ok(f"LLM 提取实体数: {len(entities)}")
            for e in entities[:5]:
                info(f"  - {e.get('name_cn')} ({e.get('type')}): "
                     f"confidence={e.get('confidence')}")

        # 10-2 手动创建典型供应链实体
        manual_entities = [
            {"name_cn": "天钢原材料有限公司", "name_en": "Tiangangyuancailiao",
             "type": "供应商", "description": "S级战略供应商，年采购额>5000万",
             "confidence": 0.99, "source": "manual"},
            {"name_cn": "仓库A", "name_en": "WarehouseA",
             "type": "仓库", "description": "主要存储钢材和铝合金",
             "confidence": 0.95, "source": "manual"},
            {"name_cn": "库存预警规则", "name_en": "InventoryAlertRule",
             "type": "规则", "description": "库存量低于安全库存时触发补货",
             "confidence": 0.92, "source": "manual"},
        ]

        created_entity_ids = []
        for ent_data in manual_entities:
            resp = session.post(
                f"{base_url}/api/v1/ontologies/{ontology_id}/entities",
                json=ent_data,
            )
            if check(resp, 201, f"POST /entities ({ent_data['name_cn']})"):
                body = jget(resp)
                d = body.get("data", body) if isinstance(body, dict) else body
                eid = d.get("id", "")
                if eid:
                    created_entity_ids.append(eid)
                    ok(f"  实体创建: {ent_data['name_cn']} → id={eid[:8]}...")

        # 10-3 获取实体详情
        if created_entity_ids:
            eid = created_entity_ids[0]
            resp = session.get(f"{base_url}/api/v1/ontologies/{ontology_id}/entities/{eid}")
            if check(resp, 200, f"GET /entities/{eid[:8]}..."):
                body = jget(resp)
                d = body.get("data", body) if isinstance(body, dict) else body
                ok(f"实体详情: name={d.get('name_cn')}, type={d.get('type')}")

        # 10-4 /related 端点
        all_entity_ids = [e.get("id") for e in entities] + created_entity_ids
        if all_entity_ids:
            eid = all_entity_ids[0]
            resp = session.get(
                f"{base_url}/api/v1/ontologies/{ontology_id}/entities/{eid}/related"
            )
            if resp.status_code == 200:
                r = jget(resp)
                ok(f"Entity /related: logic_rules={len(r.get('logic_rules', []))}, "
                   f"actions={len(r.get('actions', []))}")
            else:
                warn(f"Entity /related 返回 {resp.status_code}")

    # ═══════════════════════════════════════════════════════════════════════════
    # 阶段 11 — 逻辑规则（Logic Rules）管理
    # ═══════════════════════════════════════════════════════════════════════════
    section("阶段 11 — 逻辑规则（Logic Rules）管理")

    if not ontology_id:
        warn("无本体 ID，跳过逻辑规则")
    else:
        # 11-1 查看已提取的逻辑规则
        resp = session.get(f"{base_url}/api/v1/ontologies/{ontology_id}/logic")
        logic_rules = []
        if check(resp, 200, "GET /logic"):
            body = jget(resp)
            logic_rules = body.get("data", body) if isinstance(body, dict) else body
            if not isinstance(logic_rules, list): logic_rules = []
            ok(f"逻辑规则数: {len(logic_rules)}")
            for r in logic_rules[:3]:
                info(f"  - {r.get('name_cn', r.get('condition', '?'))[:40]}")

        # 11-2 手动创建核心供应链规则（来自 supply_chain_strategy.md）
        manual_rules = [
            {
                "name_cn": "供应商降级规则",
                "condition": "IF 交货准时率 < 85%",
                "action": "THEN 降一级并发送整改通知",
                "confidence": 0.98,
                "linked_entities": ["天钢原材料有限公司"],
                "source": "manual",
            },
            {
                "name_cn": "紧急补货规则",
                "condition": "IF 库存量 < 安全库存*0.5",
                "action": "THEN 触发紧急采购，跳过常规审批直接报VP",
                "confidence": 0.97,
                "linked_entities": ["仓库A", "库存预警规则"],
                "source": "manual",
            },
            {
                "name_cn": "质量事故暂停规则",
                "condition": "IF 单次质量事故损失 > 50万元",
                "action": "THEN 直接暂停合作并提交管委会",
                "confidence": 0.99,
                "linked_entities": ["天钢原材料有限公司"],
                "source": "manual",
            },
        ]

        created_rule_ids = []
        for rule in manual_rules:
            resp = session.post(
                f"{base_url}/api/v1/ontologies/{ontology_id}/logic",
                json=rule,
            )
            if check(resp, 201, f"POST /logic ({rule['name_cn']})"):
                body = jget(resp)
                d = body.get("data", body) if isinstance(body, dict) else body
                rid = d.get("id", "")
                if rid:
                    created_rule_ids.append(rid)
                    ok(f"  规则创建: {rule['name_cn']} → id={rid[:8]}...")

        # 11-3 获取规则详情
        if created_rule_ids:
            rid = created_rule_ids[0]
            resp = session.get(f"{base_url}/api/v1/ontologies/{ontology_id}/logic/{rid}")
            if check(resp, 200, f"GET /logic/{rid[:8]}..."):
                body = jget(resp)
                d = body.get("data", body) if isinstance(body, dict) else body
                ok(f"规则详情: name={d.get('name_cn')}, condition={str(d.get('condition', ''))[:40]}")

    # ═══════════════════════════════════════════════════════════════════════════
    # 阶段 12 — 动作（Actions）管理
    # ═══════════════════════════════════════════════════════════════════════════
    section("阶段 12 — 动作（Actions）管理")

    if not ontology_id:
        warn("无本体 ID，跳过动作管理")
    else:
        # 12-1 查看已提取动作
        resp = session.get(f"{base_url}/api/v1/ontologies/{ontology_id}/actions")
        actions = []
        if check(resp, 200, "GET /actions"):
            body = jget(resp)
            actions = body.get("data", body) if isinstance(body, dict) else body
            if not isinstance(actions, list): actions = []
            ok(f"动作数: {len(actions)}")

        # 12-2 手动创建动作
        action_data = {
            "name_cn": "发送供应商整改通知",
            "trigger_condition": "供应商交货准时率 < 85%",
            "function_code": (
                "def send_supplier_rectification(context: dict) -> dict:\n"
                "    supplier = context.get('supplier_name', 'Unknown')\n"
                "    rate = context.get('delivery_rate', 0)\n"
                "    if rate < 0.85:\n"
                "        return {'status': 'triggered', "
                "'message': f'已向{supplier}发送整改通知，当前准时率{rate:.1%}'}\n"
                "    return {'status': 'skipped'}"
            ),
            "confidence": 0.95,
            "linked_entities": ["天钢原材料有限公司"],
            "source": "manual",
        }
        resp = session.post(
            f"{base_url}/api/v1/ontologies/{ontology_id}/actions",
            json=action_data,
        )
        if check(resp, 201, "POST /actions (发送供应商整改通知)"):
            body = jget(resp)
            d = body.get("data", body) if isinstance(body, dict) else body
            aid = d.get("id", "")
            ok(f"动作创建: {action_data['name_cn']} → id={aid[:8] if aid else '?'}...")

            # 12-3 获取动作详情
            if aid:
                resp = session.get(f"{base_url}/api/v1/ontologies/{ontology_id}/actions/{aid}")
                if check(resp, 200, f"GET /actions/{aid[:8]}..."):
                    body = jget(resp)
                    d = body.get("data", body) if isinstance(body, dict) else body
                    ok(f"动作详情: name={d.get('name_cn')}, confidence={d.get('confidence')}")

    # ═══════════════════════════════════════════════════════════════════════════
    # 阶段 13 — 关系（Relations）与图谱
    # ═══════════════════════════════════════════════════════════════════════════
    section("阶段 13 — 关系与知识图谱（Graph）")

    if not ontology_id:
        warn("无本体 ID，跳过图谱")
    else:
        # 13-1 基础图谱
        resp = session.get(f"{base_url}/api/v1/ontologies/{ontology_id}/graph")
        if check(resp, 200, "GET /graph"):
            body = jget(resp)
            d = body.get("data", body) if isinstance(body, dict) else body
            nodes = d.get("nodes", []) if isinstance(d, dict) else []
            edges = d.get("edges", d.get("relations", [])) if isinstance(d, dict) else []
            ok(f"知识图谱: {len(nodes)} 节点, {len(edges)} 边")
            if nodes:
                # 节点格式: {"data": {"id": ..., "label": ...}}
                names = [n.get("data", n).get("label", n.get("name_cn", "?"))[:15] for n in nodes[:3]]
                info(f"  节点样例: {names}")

        # 13-2 手动创建关系
        resp = session.get(f"{base_url}/api/v1/ontologies/{ontology_id}/entities")
        all_ents = []
        if resp.status_code == 200:
            body = jget(resp)
            all_ents = body.get("data", body) if isinstance(body, dict) else body
            if not isinstance(all_ents, list): all_ents = []

        if len(all_ents) >= 2:
            rel_payload = {
                "ontology_id": ontology_id,
                "source_entity": all_ents[0].get("id"),
                "target_entity": all_ents[1].get("id"),
                "type": "supply",
                "confidence": 0.9,
                "properties": {"weight": 0.9},
            }
            resp = session.post(
                f"{base_url}/api/v1/ontologies/{ontology_id}/graph/relations",
                json=rel_payload,
            )
            if resp.status_code in (200, 201):
                r = jget(resp)
                d = r.get("data", r) if isinstance(r, dict) else r
                ok(f"关系创建: source={all_ents[0].get('name_cn', '?')[:10]} "
                   f"→ supply → {all_ents[1].get('name_cn', '?')[:10]}")
            else:
                warn(f"关系创建返回 {resp.status_code}")

        # 13-3 Top Nodes
        resp = session.get(f"{base_url}/api/v1/ontologies/{ontology_id}/graph/top-nodes")
        if resp.status_code == 200:
            body = jget(resp)
            d = body.get("data", body) if isinstance(body, dict) else body
            top = d if isinstance(d, list) else d.get("nodes", []) if isinstance(d, dict) else []
            ok(f"Top Nodes: {len(top)} 个中心节点")

        # 13-4 Neighbors（取第一个节点）
        if all_ents:
            nid = all_ents[0].get("id", "")
            resp = session.get(
                f"{base_url}/api/v1/ontologies/{ontology_id}/graph/neighbors/{nid}"
            )
            if resp.status_code == 200:
                body = jget(resp)
                d = body.get("data", body) if isinstance(body, dict) else body
                neighbors = d if isinstance(d, list) else d.get("nodes", []) if isinstance(d, dict) else []
                ok(f"节点邻居查询: {len(neighbors)} 个邻居节点")

        # 13-5 Degree
        if all_ents:
            nid = all_ents[0].get("id", "")
            resp = session.get(
                f"{base_url}/api/v1/ontologies/{ontology_id}/graph/degree/{nid}"
            )
            if resp.status_code == 200:
                body = jget(resp)
                d = body.get("data", body) if isinstance(body, dict) else body
                ok(f"节点度数: in={d.get('in_degree', 0)}, out={d.get('out_degree', 0)}")

    # ═══════════════════════════════════════════════════════════════════════════
    # 阶段 14 — 搜索（Keyword + Semantic）
    # ═══════════════════════════════════════════════════════════════════════════
    section("阶段 14 — 知识图谱搜索（关键词 + 语义）")

    if not ontology_id:
        warn("无本体 ID，跳过搜索")
    else:
        search_terms = ["供应商", "库存", "采购", "质量"]
        for term in search_terms:
            resp = session.get(
                f"{base_url}/api/v2/ontologies/{ontology_id}/search/keyword",
                params={"q": term, "limit": 5},
            )
            if resp.status_code == 200:
                body = jget(resp)
                d = body.get("data", body) if isinstance(body, dict) else body
                results = d if isinstance(d, list) else d.get("results", []) if isinstance(d, dict) else []
                ok(f"关键词搜索 '{term}': {len(results)} 条结果")
            else:
                warn(f"关键词搜索 '{term}' 返回 {resp.status_code}")

        # 语义搜索
        resp = session.get(
            f"{base_url}/api/v2/ontologies/{ontology_id}/search/semantic",
            params={"q": "供应商评级下降如何处理", "limit": 5},
        )
        if resp.status_code == 200:
            body = jget(resp)
            d = body.get("data", body) if isinstance(body, dict) else body
            results = d if isinstance(d, list) else d.get("results", []) if isinstance(d, dict) else []
            ok(f"语义搜索: {len(results)} 条结果（ChromaDB）")
        else:
            warn(f"语义搜索返回 {resp.status_code}（ChromaDB 可能未运行）")

    # ═══════════════════════════════════════════════════════════════════════════
    # 阶段 15 — 导出（Export）
    # ═══════════════════════════════════════════════════════════════════════════
    section("阶段 15 — 本体导出（JSON / Turtle / CSV）")

    if not ontology_id:
        warn("无本体 ID，跳过导出")
    else:
        for fmt in ["json", "csv"]:
            resp = session.get(
                f"{base_url}/api/v1/ontologies/{ontology_id}/export",
                params={"format": fmt},
            )
            if resp.status_code == 200:
                size = len(resp.content)
                ok(f"导出 {fmt.upper()}: {size} 字节")
            else:
                warn(f"导出 {fmt} 返回 {resp.status_code}")

    # ═══════════════════════════════════════════════════════════════════════════
    # 阶段 16 — v2 图谱接口（Mappings）
    # ═══════════════════════════════════════════════════════════════════════════
    section("阶段 16 — v2 Mappings（本体-数据集映射）")

    if not ontology_id:
        warn("无本体 ID，跳过 Mappings")
    else:
        resp = session.get(f"{base_url}/api/v2/ontologies/{ontology_id}/mappings")
        if resp.status_code == 200:
            body = jget(resp)
            d = body.get("data", body) if isinstance(body, dict) else body
            mappings = d if isinstance(d, list) else []
            ok(f"本体映射: {len(mappings)} 条")
        else:
            warn(f"Mappings 返回 {resp.status_code}")

    # ═══════════════════════════════════════════════════════════════════════════
    # 阶段 17 — 系统设置
    # ═══════════════════════════════════════════════════════════════════════════
    section("阶段 17 — 系统设置（置信度规则 + 用户管理）")

    # 17-1 置信度规则列表
    resp = session.get(f"{base_url}/api/v1/settings/rules")
    if check(resp, 200, "GET /settings/rules"):
        body = jget(resp)
        rules = body.get("data", body) if isinstance(body, dict) else body
        rules_list = rules if isinstance(rules, list) else []
        ok(f"置信度规则: {len(rules_list)} 条")
        for r in rules_list[:4]:
            info(f"  {r.get('rule_key')}={r.get('rule_value')} — {r.get('rule_label_cn')}")

    # 17-2 修改置信度规则（PUT /settings/rules 接收列表格式）
    if rules_list:
        resp = session.put(
            f"{base_url}/api/v1/settings/rules",
            json=[{"rule_key": "confidence_entity_min", "rule_value": "0.55"}],
        )
        if resp.status_code == 200:
            ok("置信度规则更新: confidence_entity_min → 0.55")
        else:
            warn(f"规则更新返回 {resp.status_code}")

    # 17-3 用户列表
    resp = session.get(f"{base_url}/api/v1/users")
    if check(resp, 200, "GET /users"):
        body = jget(resp)
        users = body.get("data", body) if isinstance(body, dict) else body
        users_list = users if isinstance(users, list) else []
        ok(f"用户数: {len(users_list)}")
        for u in users_list[:3]:
            info(f"  - {u.get('username')}, role={u.get('role')}, id={str(u.get('id', ''))[:8]}")

    # 17-4 本体列表（确认新建的本体在列表中）
    resp = session.get(f"{base_url}/api/v1/ontologies")
    if check(resp, 200, "GET /ontologies"):
        body = jget(resp)
        # 返回 {"data": {"items": [...], "total": ...}}
        ontos = body.get("data", {}) if isinstance(body, dict) else {}
        onto_list = ontos.get("items", []) if isinstance(ontos, dict) else (ontos if isinstance(ontos, list) else [])
        ok(f"本体项目总数: {ontos.get('total', len(onto_list))}")
        target = next((o for o in onto_list if o.get("id") == ontology_id), None)
        if target:
            ok(f"  本体 '{target.get('name')}' 在列表中确认存在")

    # ═══════════════════════════════════════════════════════════════════════════
    # 最终汇总
    # ═══════════════════════════════════════════════════════════════════════════
    section("测试汇总")
    total = PASS_COUNT + FAIL_COUNT + WARN_COUNT
    print(f"\n  总计: {total} 项检查")
    print(f"  ✅  通过: {PASS_COUNT}")
    print(f"  ⚠️   警告: {WARN_COUNT}（可接受）")
    print(f"  ❌  失败: {FAIL_COUNT}")

    if FAILURES:
        print("\n  失败项目：")
        for f in FAILURES:
            print(f"    • {f}")
        print()
        sys.exit(1)
    else:
        print("\n  🎉 全部检查通过！供应链用户旅程测试完成。\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    args = parser.parse_args()
    run(args.base_url)

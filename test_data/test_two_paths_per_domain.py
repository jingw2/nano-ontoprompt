#!/usr/bin/env python3
"""两条路径 × 三业务域 正确测试

修正点: Pipeline Mapping 每个业务域只建 **一个** pipeline, 其 connector 节点
以「文件上传」方式挂载该域文件夹的全部文件 (config.files[]), 后端按每个文件
的 kind 自动选 A/B/C 路由, 产出多个 curated dataset。

简易 LLM: 每域一个 ontology, 上传该域全部可文本化文件后一次性提取。

前置: 后端 8000 + mock LLM 8123 (test_data/mock_llm_server.py) 运行中。
"""
import re
import sys
import time
from collections import Counter
from pathlib import Path

import requests

API = "http://localhost:8000"
TD = Path(__file__).parent
DOMAINS = ["供应链", "财务", "医疗"]
# 简易 LLM 只处理可文本化文件
TEXT_EXT = {".md", ".docx", ".doc", ".pdf", ".pptx", ".ppt", ".txt", ".csv", ".xlsx"}

s = requests.Session()
r = s.post(f"{API}/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
s.headers.update({"Authorization": f"Bearer {r.json()['data']['access_token']}"})
print("✅ 登录")


def pascal(fname: str) -> str:
    base = fname.rsplit(".", 1)[0]
    parts = re.split(r"[_\-\s]+", base)
    return "".join(p[:1].upper() + p[1:] for p in parts if p)[:40] or "Entity"


def clean_ontologies():
    ontos = s.get(f"{API}/api/v1/ontologies").json().get("data", [])
    if isinstance(ontos, dict):
        ontos = ontos.get("items", [])
    for o in ontos:
        s.delete(f"{API}/api/v1/ontologies/{o['id']}")
    print(f"🧹 清理 {len(ontos)} 个旧本体")


def ensure_model_prompt():
    models = s.get(f"{API}/api/v1/models").json().get("data", [])
    mock = next((m for m in models if m.get("name") == "Mock Extractor (测试)"), None)
    if not mock:
        mock = s.post(f"{API}/api/v1/models", json={
            "name": "Mock Extractor (测试)", "provider": "compatible",
            "api_key": "mock-key", "api_base": "http://127.0.0.1:8123/v1",
            "models": ["mock-extractor"]}).json()["data"]
    prompts = s.get(f"{API}/api/v1/prompts").json().get("data", [])
    pr = next((p for p in prompts if p.get("name") == "通用本体提取(测试)"), None)
    if not pr:
        pr = s.post(f"{API}/api/v1/prompts", json={
            "name": "通用本体提取(测试)", "domain": "通用",
            "content": "提取 entities/relations/logic_rules/actions, 只返回 JSON。"}).json()["data"]
    return mock["id"], pr["id"]


# ─────────────────────────── Pipeline Mapping ───────────────────────────
def run_mapping(domain: str) -> dict:
    folder = TD / domain
    files = sorted(folder.glob("*"))
    # 1) 上传该域全部文件, 收集 connector.files
    conn_files = []
    for f in files:
        with open(f, "rb") as fh:
            r = s.post(f"{API}/api/v2/datasets/upload", files={"file": (f.name, fh)})
        j = r.json().get("data") or r.json()
        conn_files.append({"name": f.name, "dataset_id": j["id"], "kind": j.get("kind")})

    # 2) 单个 pipeline, connector 挂全部文件 (贴合前端 definition 结构)
    definition = {
        "nodes": [
            {"id": "conn", "type": "connector", "position": {"x": 0, "y": 0}, "label": "连接器",
             "config": {"source_type": "file", "files": conn_files}},
            {"id": "stor", "type": "storage", "position": {"x": 260, "y": 0}, "label": "存储器",
             "config": {"storage_mode": "auto"}},
            {"id": "tran", "type": "transform", "position": {"x": 520, "y": 0}, "label": "转换器",
             "config": {"path": "auto", "steps": []}},
            {"id": "out", "type": "output", "position": {"x": 780, "y": 0}, "label": "输出",
             "config": {"dataset_type": "curated_dataset", "primary_key": []}},
        ],
        "edges": [{"id": "e1", "source": "conn", "target": "stor"},
                  {"id": "e2", "source": "stor", "target": "tran"},
                  {"id": "e3", "source": "tran", "target": "out"}],
    }
    r = s.post(f"{API}/api/v2/pipelines", json={
        "name": f"{domain}-管道-{int(time.time())}", "definition": definition})
    pid = (r.json().get("data") or r.json())["id"]
    print(f"\n══ {domain} Mapping: 1 个 pipeline, connector 挂 {len(conn_files)} 个文件 ══")

    res = s.post(f"{API}/api/v2/pipelines/{pid}/run-sync").json()
    outputs = ((res.get("stats") or {}).get("meta") or {}).get("outputs", [])
    print(f"  run-sync: {res.get('status')}, 产出 {len(outputs)} 个 curated dataset")
    for o in outputs:
        print(f"    {o.get('source_file'):30s} route={o.get('route')} rows={o.get('rows_out')}")

    # 3) 审批 + 建映射 (pk 交给后端自动推断)
    r = s.post(f"{API}/api/v1/ontologies", json={
        "name": f"{domain}-Mapping-{int(time.time())}", "domain": domain,
        "build_mode": "pipeline_mapping", "description": "单管道多文件 connector 测试"})
    oid = r.json()["data"]["id"]
    for o in outputs:
        cid = o["curated_dataset_id"]
        s.post(f"{API}/api/v2/curated/{cid}/review", params={"action": "approve"})
        s.post(f"{API}/api/v2/ontologies/{oid}/mappings", json={
            "curated_dataset_id": cid, "entity_class": pascal(o["source_file"]),
            "field_mapping": {}})
    rb = s.post(f"{API}/api/v2/ontologies/{oid}/mappings/build-all")
    res2 = rb.json()
    g = s.get(f"{API}/api/v2/ontologies/{oid}/graph?limit=1000").json()
    g = g.get("data", g)
    lbl = {n["id"]: (n.get("labels") or ["?"])[0] for n in g["nodes"]}
    pairs = Counter((lbl.get(e["source"], "?"), e["type"], lbl.get(e["target"], "?")) for e in g["edges"])
    print(f"  build-all: {rb.status_code} 实体={res2.get('total_entities')} 关系={res2.get('total_relations')} "
          f"逻辑={res2.get('total_logic')} 动作={res2.get('total_actions')}")
    print(f"  图谱: {len(g['nodes'])} 节点 {len(g['edges'])} 边")
    for k, v in pairs.most_common():
        print(f"    {k[0]} -[{k[1]}]-> {k[2]} × {v}")
    return {"domain": domain, "oid": oid, "pipeline_id": pid, "files": len(conn_files),
            "curated": len(outputs), "nodes": len(g["nodes"]), "edges": len(g["edges"]),
            "logic": res2.get("total_logic"), "actions": res2.get("total_actions")}


# ─────────────────────────── 简易 LLM 提取 ───────────────────────────
def run_simple(domain: str, model_id: str, prompt_id: str) -> dict:
    folder = TD / domain
    files = [f for f in sorted(folder.glob("*")) if f.suffix.lower() in TEXT_EXT]
    r = s.post(f"{API}/api/v1/ontologies", json={
        "name": f"{domain}-简易LLM-{int(time.time())}", "domain": domain,
        "build_mode": "simple_llm", "description": "上传整域文件后一次性 LLM 提取"})
    oid = r.json()["data"]["id"]
    print(f"\n══ {domain} 简易 LLM: 上传 {len(files)} 个文件 ══")
    for f in files:
        with open(f, "rb") as fh:
            s.post(f"{API}/api/v1/ontologies/{oid}/files", files={"file": (f.name, fh)})

    r = s.post(f"{API}/api/v1/ontologies/{oid}/execute", json={
        "prompt_id": prompt_id, "model_id": model_id, "model_name": "mock-extractor"})
    tid = r.json()["data"]["task_id"]
    status = "running"
    for _ in range(90):
        time.sleep(2)
        task = s.get(f"{API}/api/v1/ontologies/{oid}/execute/status", params={"task_id": tid}).json().get("data", {})
        status = task.get("status")
        if status in ("success", "failed", "completed"):
            break
    ents = s.get(f"{API}/api/v1/ontologies/{oid}/entities", params={"page_size": 300}).json()
    d = ents.get("data", ents)
    items = d.get("items", d) if isinstance(d, dict) else d
    g = s.get(f"{API}/api/v2/ontologies/{oid}/graph?limit=800").json()
    g = g.get("data", g)
    logic = len(s.get(f"{API}/api/v1/ontologies/{oid}/logic").json().get("data", []))
    acts = len(s.get(f"{API}/api/v1/ontologies/{oid}/actions").json().get("data", []))
    print(f"  提取: {status} 实体={len(items)} 边={len(g.get('edges', []))} 逻辑={logic} 动作={acts}")
    return {"domain": domain, "oid": oid, "files": len(files), "status": status,
            "nodes": len(items), "edges": len(g.get("edges", [])), "logic": logic, "actions": acts}


if __name__ == "__main__":
    clean_ontologies()
    model_id, prompt_id = ensure_model_prompt()
    mapping_res = [run_mapping(d) for d in DOMAINS]
    simple_res = [run_simple(d, model_id, prompt_id) for d in DOMAINS]

    print("\n" + "=" * 60)
    print("Pipeline Mapping 汇总 (每域 1 个 pipeline)")
    for r in mapping_res:
        print(f"  ✅ {r['domain']}: {r['files']}文件→1管道→{r['curated']}curated | "
              f"{r['nodes']}节点 {r['edges']}边 logic={r['logic']} actions={r['actions']} | {r['oid']}")
    print("简易 LLM 汇总")
    for r in simple_res:
        print(f"  {'✅' if r['nodes'] > 0 else '❌'} {r['domain']}: {r['files']}文件 {r['status']} | "
              f"{r['nodes']}节点 {r['edges']}边 logic={r['logic']} actions={r['actions']} | {r['oid']}")

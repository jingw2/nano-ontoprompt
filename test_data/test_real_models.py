#!/usr/bin/env python3
"""真实模型 (deepseek-v4) 两条路径 × 三域测试

- 简易 LLM: 每域上传全部可文本化文件, deepseek-v4 一次性提取
- Pipeline Mapping: 每域单 pipeline + connector 挂全文件夹; Route C 文档
  由 deepseek-v4 做 LLM 自动结构化提取 (非规则兜底), 结构化数据正常映射
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
TEXT_EXT = {".md", ".docx", ".doc", ".pdf", ".pptx", ".ppt", ".txt", ".csv", ".xlsx"}

s = requests.Session()
r = s.post(f"{API}/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
s.headers.update({"Authorization": f"Bearer {r.json()['data']['access_token']}"})
models = s.get(f"{API}/api/v1/models").json().get("data", [])
ds = next(m for m in models if m["name"] == "deepseek-v4")
DS_ID, DS_MODEL = ds["id"], (ds.get("models") or ["deepseek-v4-flash"])[0]
prompts = s.get(f"{API}/api/v1/prompts").json().get("data", [])
PROMPT_ID = prompts[0]["id"]
print(f"✅ 模型 deepseek-v4 ({DS_MODEL}), prompt {PROMPT_ID[:8]}")


def pascal(fname: str) -> str:
    parts = re.split(r"[_\-\s]+", fname.rsplit(".", 1)[0])
    return "".join(p[:1].upper() + p[1:] for p in parts if p)[:40] or "Entity"


def wait_task(oid, tid, label):
    for i in range(120):
        time.sleep(3)
        t = s.get(f"{API}/api/v1/ontologies/{oid}/execute/status", params={"task_id": tid}).json().get("data", {})
        st = t.get("status")
        if st in ("success", "failed", "completed"):
            if st == "failed":
                print(f"    ❌ {label} error: {str(t.get('error'))[:200]}")
            return st, i * 3
    return "timeout", 360


def run_simple(domain):
    files = [f for f in sorted((TD / domain).glob("*")) if f.suffix.lower() in TEXT_EXT]
    oid = s.post(f"{API}/api/v1/ontologies", json={
        "name": f"{domain}-简易LLM-真实-{int(time.time())}", "domain": domain,
        "build_mode": "simple_llm"}).json()["data"]["id"]
    for f in files:
        with open(f, "rb") as fh:
            s.post(f"{API}/api/v1/ontologies/{oid}/files", files={"file": (f.name, fh)})
    r = s.post(f"{API}/api/v1/ontologies/{oid}/execute", json={
        "prompt_id": PROMPT_ID, "model_id": DS_ID, "model_name": DS_MODEL})
    tid = r.json()["data"]["task_id"]
    st, dt = wait_task(oid, tid, f"{domain}简易")
    ents = s.get(f"{API}/api/v1/ontologies/{oid}/entities", params={"page_size": 300}).json()
    d = ents.get("data", ents); items = d.get("items", d) if isinstance(d, dict) else d
    g = s.get(f"{API}/api/v2/ontologies/{oid}/graph?limit=800").json(); g = g.get("data", g)
    types = Counter(e.get("type") for e in items)
    logic = len(s.get(f"{API}/api/v1/ontologies/{oid}/logic").json().get("data", []))
    acts = len(s.get(f"{API}/api/v1/ontologies/{oid}/actions").json().get("data", []))
    print(f"  简易 {domain}: {st} ({dt}s) {len(files)}文件 → {len(items)}实体 {len(g.get('edges',[]))}边 "
          f"logic={logic} actions={acts}")
    print(f"    实体类型: {dict(types)}")
    return {"domain": domain, "oid": oid, "status": st, "sec": dt, "files": len(files),
            "nodes": len(items), "edges": len(g.get("edges", [])), "logic": logic, "actions": acts,
            "types": dict(types)}


def run_mapping(domain):
    files = sorted((TD / domain).glob("*"))
    conn_files = []
    for f in files:
        with open(f, "rb") as fh:
            j = s.post(f"{API}/api/v2/datasets/upload", files={"file": (f.name, fh)}).json()
        j = j.get("data") or j
        conn_files.append({"name": f.name, "dataset_id": j["id"], "kind": j.get("kind")})
    definition = {"nodes": [
        {"id": "conn", "type": "connector", "position": {"x": 0, "y": 0}, "label": "连接器",
         "config": {"source_type": "file", "files": conn_files}},
        {"id": "stor", "type": "storage", "position": {"x": 260, "y": 0}, "label": "存储器", "config": {"storage_mode": "auto"}},
        {"id": "tran", "type": "transform", "position": {"x": 520, "y": 0}, "label": "转换器", "config": {"path": "auto", "steps": []}},
        {"id": "out", "type": "output", "position": {"x": 780, "y": 0}, "label": "输出", "config": {"dataset_type": "curated_dataset"}}],
        "edges": [{"id": "e1", "source": "conn", "target": "stor"}, {"id": "e2", "source": "stor", "target": "tran"},
                  {"id": "e3", "source": "tran", "target": "out"}]}
    pid = s.post(f"{API}/api/v2/pipelines", json={
        "name": f"{domain}-管道-真实-{int(time.time())}", "definition": definition}).json()
    pid = (pid.get("data") or pid)["id"]
    t0 = time.time()
    res = s.post(f"{API}/api/v2/pipelines/{pid}/run-sync").json()
    dt = round(time.time() - t0)
    outputs = ((res.get("stats") or {}).get("meta") or {}).get("outputs", [])
    methods = Counter()
    for o in outputs:
        methods[o.get("route")] += 1
    oid = s.post(f"{API}/api/v1/ontologies", json={
        "name": f"{domain}-Mapping-真实-{int(time.time())}", "domain": domain,
        "build_mode": "pipeline_mapping"}).json()["data"]["id"]
    for o in outputs:
        s.post(f"{API}/api/v2/curated/{o['curated_dataset_id']}/review", params={"action": "approve"})
        s.post(f"{API}/api/v2/ontologies/{oid}/mappings", json={
            "curated_dataset_id": o["curated_dataset_id"], "entity_class": pascal(o["source_file"]), "field_mapping": {}})
    rb = s.post(f"{API}/api/v2/ontologies/{oid}/mappings/build-all").json()
    g = s.get(f"{API}/api/v2/ontologies/{oid}/graph?limit=1000").json(); g = g.get("data", g)
    print(f"  Mapping {domain}: run {res.get('status')} ({dt}s) {len(conn_files)}文件→{len(outputs)}curated "
          f"(路由 {dict(methods)})")
    print(f"    build-all 实体={rb.get('total_entities')} 关系={rb.get('total_relations')} "
          f"logic={rb.get('total_logic')} actions={rb.get('total_actions')} | 图谱 {len(g['nodes'])}节点 {len(g['edges'])}边")
    return {"domain": domain, "oid": oid, "run_status": res.get("status"), "sec": dt,
            "files": len(conn_files), "curated": len(outputs),
            "nodes": len(g["nodes"]), "edges": len(g["edges"]),
            "logic": rb.get("total_logic"), "actions": rb.get("total_actions")}


if __name__ == "__main__":
    print("\n###### 简易 LLM (deepseek-v4) ######")
    simple = [run_simple(d) for d in DOMAINS]
    print("\n###### Pipeline Mapping (Route C 用 deepseek-v4) ######")
    mapping = [run_mapping(d) for d in DOMAINS]
    print("\n" + "=" * 64)
    print("简易 LLM 汇总:")
    for r in simple:
        print(f"  {'✅' if r['nodes']>0 else '❌'} {r['domain']}: {r['status']} {r['sec']}s | "
              f"{r['nodes']}实体 {r['edges']}边 logic={r['logic']} actions={r['actions']} | {r['oid']}")
    print("Pipeline Mapping 汇总:")
    for r in mapping:
        print(f"  {'✅' if r['nodes']>0 else '❌'} {r['domain']}: {r['run_status']} {r['sec']}s | "
              f"{r['files']}文件→{r['curated']}curated | {r['nodes']}节点 {r['edges']}边 "
              f"logic={r['logic']} actions={r['actions']} | {r['oid']}")

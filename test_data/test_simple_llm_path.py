#!/usr/bin/env python3
"""简易 LLM 提取路径 — 三业务域测试驱动

前置: 后端 8000 运行中, mock LLM 8123 运行中 (mock_llm_server.py)。
流程: 注册模型/提示词 → 每域: 建本体(simple_llm) → 传文档 → 提取 → 验证。
"""
import sys
import time
from pathlib import Path

import requests

API = "http://localhost:8000"
TD = Path(__file__).parent

DOMAINS = {
    "供应链": ["供应链/supply_chain_strategy.md", "供应链/procurement_policy.docx"],
    "医疗": ["医疗/clinical_protocols.md", "医疗/treatment_procedures.docx"],
    "财务": ["财务/financial_controls.md", "财务/month_end_close.docx"],
}

PROMPT_CONTENT = """你是本体工程专家。请从文档中提取:
1. entities: 实体列表 [{name_cn, name_en, type, description, properties, confidence}]
2. relations: 关系 [{source, target, type}] (source/target 用实体 name_cn)
3. logic_rules: 逻辑规则 [{name_cn, name_en, description, formula, linked_entities, confidence}]
4. actions: 动作 [{name_cn, description, trigger_condition, linked_entities, confidence}]
只返回 JSON。"""

s = requests.Session()
r = s.post(f"{API}/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
s.headers.update({"Authorization": f"Bearer {r.json()['data']['access_token']}"})
print("✅ 登录")

# 模型 (mock) — 已存在则复用
models = s.get(f"{API}/api/v1/models").json().get("data", [])
mock = next((m for m in models if m.get("name") == "Mock Extractor (测试)"), None)
if not mock:
    r = s.post(f"{API}/api/v1/models", json={
        "name": "Mock Extractor (测试)", "provider": "compatible",
        "api_key": "mock-key", "api_base": "http://127.0.0.1:8123/v1",
        "models": ["mock-extractor"],
    })
    mock = r.json()["data"]
model_id = mock["id"]
print(f"✅ 模型: {model_id[:8]}")

# 提示词 — 已存在则复用
prompts = s.get(f"{API}/api/v1/prompts").json().get("data", [])
pr = next((p for p in prompts if p.get("name") == "通用本体提取(测试)"), None)
if not pr:
    r = s.post(f"{API}/api/v1/prompts", json={
        "name": "通用本体提取(测试)", "domain": "通用", "content": PROMPT_CONTENT,
    })
    pr = r.json()["data"]
prompt_id = pr["id"]
print(f"✅ 提示词: {prompt_id[:8]}")

results = []
for domain, files in DOMAINS.items():
    print(f"\n══ {domain} (简易 LLM) ══")
    r = s.post(f"{API}/api/v1/ontologies", json={
        "name": f"{domain}-简易LLM-{int(time.time())}", "domain": domain,
        "build_mode": "simple_llm", "description": "简易 LLM 路径测试 (mock LLM)",
    })
    oid = r.json()["data"]["id"]
    print(f"  ontology: {oid[:8]}")

    for f in files:
        p = TD / f
        with open(p, "rb") as fh:
            r = s.post(f"{API}/api/v1/ontologies/{oid}/files", files={"file": (p.name, fh)})
        print(f"  上传 {p.name}: {r.status_code}")

    r = s.post(f"{API}/api/v1/ontologies/{oid}/execute", json={
        "prompt_id": prompt_id, "model_id": model_id, "model_name": "mock-extractor",
    })
    if not r.ok:
        print(f"  ❌ execute: {r.status_code} {r.text[:150]}")
        results.append((domain, oid, "execute_failed", {}))
        continue
    task_id = r.json()["data"]["task_id"]

    status, task = "running", {}
    for _ in range(60):
        time.sleep(2)
        task = s.get(f"{API}/api/v1/ontologies/{oid}/execute/status",
                     params={"task_id": task_id}).json().get("data", {})
        status = task.get("status")
        if status in ("success", "failed", "completed"):
            break
    print(f"  提取: {status} {('err=' + str(task.get('error'))[:120]) if status == 'failed' else ''}")

    ents = s.get(f"{API}/api/v1/ontologies/{oid}/entities", params={"page_size": 200}).json()
    d = ents.get("data", ents)
    items = d.get("items", d) if isinstance(d, dict) else d
    g = s.get(f"{API}/api/v2/ontologies/{oid}/graph?limit=500").json()
    g = g.get("data", g)
    logic = s.get(f"{API}/api/v2/ontologies/{oid}/logic").json()
    acts = s.get(f"{API}/api/v2/ontologies/{oid}/actions").json()
    stats = {"entities": len(items), "edges": len(g.get("edges", [])),
             "logic_v1": len(s.get(f"{API}/api/v1/ontologies/{oid}/logic").json().get("data", [])),
             "actions_v1": len(s.get(f"{API}/api/v1/ontologies/{oid}/actions").json().get("data", []))}
    print(f"  实体={stats['entities']} 边={stats['edges']} 逻辑={stats['logic_v1']} 动作={stats['actions_v1']}")
    results.append((domain, oid, status, stats))

print("\n══ 简易路径汇总 ══")
ok = True
for domain, oid, status, stats in results:
    flag = "✅" if status in ("success", "completed") and stats.get("entities", 0) > 0 else "❌"
    ok = ok and flag == "✅"
    print(f"{flag} {domain}: {oid[:8]} status={status} {stats}")
sys.exit(0 if ok else 1)

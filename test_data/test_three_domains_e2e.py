#!/usr/bin/env python3
"""三业务域全链路测试：供应链 / 财务 / 医疗
  - 简易 LLM 路径（mock LLM）
  - Pipeline Mapping 路径（规则提取，默认）
前置：后端 8000 + mock LLM 8123 运行中。
"""
import re, sys, time
from pathlib import Path
import requests

API = "http://localhost:8000"
TD  = Path(__file__).parent
DOMAINS = ["供应链", "财务", "医疗"]
TEXT_EXT = {".md", ".docx", ".doc", ".pdf", ".pptx", ".ppt", ".txt", ".csv", ".xlsx", ".json"}

s = requests.Session()
r = s.post(f"{API}/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
assert r.ok, f"登录失败: {r.text}"
s.headers.update({"Authorization": f"Bearer {r.json()['data']['access_token']}"})
print("✅ 登录成功\n")


# ── 公共工具 ──────────────────────────────────────────────────────────────────

def pascal(fname: str) -> str:
    base = fname.rsplit(".", 1)[0]
    parts = re.split(r"[_\-\s]+", base)
    return "".join(p[:1].upper() + p[1:] for p in parts if p)[:40] or "Entity"


def ensure_model_prompt():
    models = s.get(f"{API}/api/v1/models").json().get("data", [])

    # 优先用真实 LLM（排除 mock 和 VLM），fallback 到 mock
    VLM_TOKENS = ("omni", "vlm", "vision", "mimo")
    real_models = [
        m for m in models
        if m.get("name") != "Mock Extractor (测试)"
        and not any(t in (m.get("name") or "").lower() for t in VLM_TOKENS)
        and not any(t in " ".join(m.get("models") or []).lower() for t in VLM_TOKENS)
    ]

    if real_models:
        chosen = real_models[0]
        model_name = (chosen.get("models") or [""])[0]
        print(f"  使用真实 LLM：{chosen['name']} / {model_name}")
    else:
        chosen = next((m for m in models if m.get("name") == "Mock Extractor (测试)"), None)
        if not chosen:
            chosen = s.post(f"{API}/api/v1/models", json={
                "name": "Mock Extractor (测试)", "provider": "compatible",
                "api_key": "mock-key", "api_base": "http://127.0.0.1:8123/v1",
                "models": ["mock-extractor"],
            }).json()["data"]
        model_name = "mock-extractor"
        print(f"  未找到真实 LLM，使用 Mock Extractor")

    PROMPT_CONTENT = """你是本体工程专家，请从文档中提取以下四类本体信息，严格按 JSON 格式返回，不要有任何其他文字：
{
  "entities": [
    {"name_cn": "实体中文名", "name_en": "EntityName", "type": "实体类型", "description": "描述", "properties": {}, "confidence": 0.9}
  ],
  "relations": [
    {"source": "源实体name_cn", "target": "目标实体name_cn", "type": "关系类型"}
  ],
  "logic_rules": [
    {"name_cn": "规则名", "name_en": "rule_name", "description": "描述", "formula": "公式", "linked_entities": ["实体name_cn"], "confidence": 0.85}
  ],
  "actions": [
    {"name_cn": "动作名", "description": "描述", "trigger_condition": "触发条件", "linked_entities": ["实体name_cn"], "confidence": 0.8}
  ]
}"""

    prompts = s.get(f"{API}/api/v1/prompts").json().get("data", [])
    pr = next((p for p in prompts if p.get("name") == "通用本体提取(测试)"), None)
    if pr:
        # 强制更新内容，避免沿用旧的简陋 prompt
        s.put(f"{API}/api/v1/prompts/{pr['id']}", json={"content": PROMPT_CONTENT})
    else:
        pr = s.post(f"{API}/api/v1/prompts", json={
            "name": "通用本体提取(测试)", "domain": "通用", "content": PROMPT_CONTENT,
        }).json()["data"]
    return chosen["id"], pr["id"], model_name


def snapshot(oid: str) -> dict:
    ents = s.get(f"{API}/api/v1/ontologies/{oid}/entities", params={"page_size": 500}).json()
    d = ents.get("data", ents)
    items = d.get("items", d) if isinstance(d, dict) else d
    graph = s.get(f"{API}/api/v2/ontologies/{oid}/graph", params={"limit": 1000}).json()
    graph = graph.get("data", graph)
    logic = s.get(f"{API}/api/v1/ontologies/{oid}/logic").json().get("data", [])
    acts  = s.get(f"{API}/api/v1/ontologies/{oid}/actions").json().get("data", [])
    by_type: dict = {}
    for e in items:
        t = e.get("type", "?")
        by_type[t] = by_type.get(t, 0) + 1
    return {
        "entities":  len(items),
        "edges":     len(graph.get("edges", [])),
        "logic":     len(logic),
        "actions":   len(acts),
        "by_type":   by_type,
    }


# ── 简易 LLM 路径 ─────────────────────────────────────────────────────────────

def run_simple(domain: str, model_id: str, prompt_id: str, model_name: str = "mock-extractor") -> dict:
    folder = TD / domain
    files  = [f for f in sorted(folder.glob("*")) if f.suffix.lower() in TEXT_EXT]
    r = s.post(f"{API}/api/v1/ontologies", json={
        "name": f"{domain}-简易LLM-{int(time.time())}",
        "domain": domain, "build_mode": "simple_llm",
    })
    oid = r.json()["data"]["id"]
    for f in files:
        with open(f, "rb") as fh:
            s.post(f"{API}/api/v1/ontologies/{oid}/files", files={"file": (f.name, fh)})

    r = s.post(f"{API}/api/v1/ontologies/{oid}/execute", json={
        "prompt_id": prompt_id, "model_id": model_id, "model_name": model_name,
    })
    if not r.ok:
        return {"domain": domain, "oid": oid, "status": "execute_failed", "error": r.text[:100]}
    tid = r.json()["data"]["task_id"]

    for _ in range(60):
        time.sleep(2)
        task = s.get(f"{API}/api/v1/ontologies/{oid}/execute/status",
                     params={"task_id": tid}).json().get("data", {})
        if task.get("status") in ("success", "failed", "completed"):
            break

    status = task.get("status", "timeout")
    snap = snapshot(oid) if status != "failed" else {}
    return {"domain": domain, "oid": oid, "status": status,
            "files": len(files), **snap}


# ── Pipeline Mapping 路径 ─────────────────────────────────────────────────────

def run_mapping(domain: str) -> dict:
    folder = TD / domain
    conn_files = []
    for f in sorted(folder.glob("*")):
        with open(f, "rb") as fh:
            j = s.post(f"{API}/api/v2/datasets/upload", files={"file": (f.name, fh)}).json()
        d = j.get("data") or j
        conn_files.append({"name": f.name, "dataset_id": d["id"], "kind": d.get("kind")})

    definition = {
        "nodes": [
            {"id": "conn", "type": "connector", "position": {"x": 0,   "y": 0}, "label": "连接器",
             "config": {"source_type": "file", "files": conn_files}},
            {"id": "stor", "type": "storage",   "position": {"x": 260, "y": 0}, "label": "存储器",
             "config": {"storage_mode": "auto"}},
            {"id": "tran", "type": "transform", "position": {"x": 520, "y": 0}, "label": "转换器",
             "config": {"path": "auto", "steps": []}},
            {"id": "out",  "type": "output",    "position": {"x": 780, "y": 0}, "label": "输出",
             "config": {"dataset_type": "curated_dataset", "primary_key": []}},
        ],
        "edges": [{"id": "e1", "source": "conn", "target": "stor"},
                  {"id": "e2", "source": "stor", "target": "tran"},
                  {"id": "e3", "source": "tran", "target": "out"}],
    }
    resp = s.post(f"{API}/api/v2/pipelines", json={
        "name": f"{domain}-管道-{int(time.time())}", "definition": definition,
    })
    pid = (resp.json().get("data") or resp.json())["id"]

    res     = s.post(f"{API}/api/v2/pipelines/{pid}/run-sync").json()
    outputs = ((res.get("stats") or {}).get("meta") or {}).get("outputs", [])

    r = s.post(f"{API}/api/v1/ontologies", json={
        "name": f"{domain}-Mapping-{int(time.time())}",
        "domain": domain, "build_mode": "pipeline_mapping",
    })
    oid = r.json()["data"]["id"]
    for o in outputs:
        cid = o["curated_dataset_id"]
        s.post(f"{API}/api/v2/curated/{cid}/review", params={"action": "approve"})
        s.post(f"{API}/api/v2/ontologies/{oid}/mappings", json={
            "curated_dataset_id": cid,
            "entity_class": pascal(o["source_file"]),
            "field_mapping": {},
        })

    rb = s.post(f"{API}/api/v2/ontologies/{oid}/mappings/build-all")
    if not rb.ok:
        return {"domain": domain, "oid": oid, "status": "build_failed",
                "error": rb.text[:100], "files": len(conn_files), "curated": len(outputs)}

    snap = snapshot(oid)
    routes = {o["source_file"]: o.get("route", "?") for o in outputs}
    return {"domain": domain, "oid": oid, "status": "success",
            "files": len(conn_files), "curated": len(outputs),
            "routes": routes, **snap}


# ── 主流程 ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    model_id, prompt_id, model_name = ensure_model_prompt()
    print(f"模型 {model_id[:8]}  model_name={model_name}  提示词 {prompt_id[:8]}\n")

    WIDTH = 60
    simple_results  = []
    mapping_results = []

    # ── 简易 LLM ──
    print("=" * WIDTH)
    print("  简易 LLM 路径")
    print("=" * WIDTH)
    for domain in DOMAINS:
        print(f"  [{domain}] 提取中...", end=" ", flush=True)
        r = run_simple(domain, model_id, prompt_id, model_name)
        simple_results.append(r)
        ok = r.get("status") in ("success", "completed") and r.get("entities", 0) > 0
        flag = "✅" if ok else "❌"
        print(f"{flag} 状态={r['status']}  "
              f"实体={r.get('entities',0)}  边={r.get('edges',0)}  "
              f"逻辑={r.get('logic',0)}  动作={r.get('actions',0)}")
        if r.get("by_type"):
            for t, cnt in sorted(r["by_type"].items(), key=lambda x: -x[1])[:5]:
                print(f"      {t}: {cnt}")

    # ── Pipeline Mapping ──
    print()
    print("=" * WIDTH)
    print("  Pipeline Mapping 路径")
    print("=" * WIDTH)
    for domain in DOMAINS:
        print(f"  [{domain}] 运行中...", end=" ", flush=True)
        r = run_mapping(domain)
        mapping_results.append(r)
        ok = r.get("status") == "success" and r.get("entities", 0) > 0
        flag = "✅" if ok else "❌"
        print(f"{flag} curated={r.get('curated',0)}  "
              f"实体={r.get('entities',0)}  边={r.get('edges',0)}  "
              f"逻辑={r.get('logic',0)}  动作={r.get('actions',0)}")
        if r.get("routes"):
            for fname, route in r["routes"].items():
                print(f"      {fname:<35} route={route}")
        if r.get("by_type"):
            print("    实体类型分布:")
            for t, cnt in sorted(r["by_type"].items(), key=lambda x: -x[1])[:6]:
                print(f"      {t}: {cnt}")

    # ── 汇总 ──
    print()
    print("=" * WIDTH)
    print("  汇总")
    print("=" * WIDTH)
    print(f"  {'域':<8} {'简易LLM':^28} {'Pipeline Mapping':^28}")
    print(f"  {'':8} {'实体':>6} {'边':>5} {'逻辑':>5} {'动作':>5}  "
          f"{'实体':>6} {'边':>5} {'逻辑':>5} {'动作':>5}")
    all_ok = True
    for s_r, m_r in zip(simple_results, mapping_results):
        domain = s_r["domain"]
        s_ok = s_r.get("status") in ("success","completed") and s_r.get("entities",0) > 0
        m_ok = m_r.get("status") == "success" and m_r.get("entities",0) > 0
        flag = "✅" if (s_ok and m_ok) else "❌"
        all_ok = all_ok and s_ok and m_ok
        print(f"  {flag} {domain:<6} "
              f"{s_r.get('entities',0):>6} {s_r.get('edges',0):>5} "
              f"{s_r.get('logic',0):>5} {s_r.get('actions',0):>5}  "
              f"{m_r.get('entities',0):>6} {m_r.get('edges',0):>5} "
              f"{m_r.get('logic',0):>5} {m_r.get('actions',0):>5}")

    print()
    print(f"  最终结果: {'✅ 全部通过' if all_ok else '❌ 存在失败'}")
    sys.exit(0 if all_ok else 1)

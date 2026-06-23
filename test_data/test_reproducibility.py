#!/usr/bin/env python3
"""供应链数据重复性测试：分别运行5次简易LLM路径和5次Pipeline Mapping路径，
比较每次提取的 ontology（实体、关系、逻辑、动作）是否一致。

前置：后端 8000 + Mock LLM 8123 运行中。
"""
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

import requests

API = "http://localhost:8000"
TD = Path(__file__).parent
DOMAIN_FOLDER = TD / "供应链"
TEXT_EXT = {".md", ".docx", ".doc", ".pdf", ".pptx", ".ppt", ".txt", ".csv", ".xlsx", ".json"}
RUNS = 5

s = requests.Session()
r = s.post(f"{API}/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
s.headers.update({"Authorization": f"Bearer {r.json()['data']['access_token']}"})
print("✅ 登录成功")

# ─── 模型 & 提示词 ───────────────────────────────────────
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


def pascal(fname: str) -> str:
    base = fname.rsplit(".", 1)[0]
    parts = re.split(r"[_\-\s]+", base)
    return "".join(p[:1].upper() + p[1:] for p in parts if p)[:40] or "Entity"


def get_ontology_snapshot(oid: str) -> dict:
    """提取 ontology 的内容快照（不含 ID 等不确定字段）"""
    ents_r = s.get(f"{API}/api/v1/ontologies/{oid}/entities", params={"page_size": 500}).json()
    d = ents_r.get("data", ents_r)
    items = d.get("items", d) if isinstance(d, dict) else d

    logic_r = s.get(f"{API}/api/v1/ontologies/{oid}/logic").json().get("data", [])
    acts_r = s.get(f"{API}/api/v1/ontologies/{oid}/actions").json().get("data", [])
    graph_r = s.get(f"{API}/api/v2/ontologies/{oid}/graph", params={"limit": 1000}).json()
    graph_r = graph_r.get("data", graph_r)

    # 实体：按 name_cn 归一化
    entity_names = sorted(set(
        (e.get("name_cn") or "").strip() for e in items if e.get("name_cn")
    ))
    entity_types = sorted(set(
        f"{(e.get('name_cn') or '').strip()}:{(e.get('type') or '').strip()}"
        for e in items if e.get("name_cn")
    ))

    # 关系：按类型归一化（图谱边）
    edges = graph_r.get("edges", [])
    nodes = {n["id"]: (n.get("labels") or ["?"])[0] for n in graph_r.get("nodes", [])}
    edge_types = sorted(Counter(
        f"{nodes.get(e.get('source',''),'?')}-[{e.get('type','?')}]->{nodes.get(e.get('target',''),'?')}"
        for e in edges
    ).keys())

    # 逻辑规则
    logic_names = sorted(set((lr.get("name_cn") or "").strip() for lr in logic_r if lr.get("name_cn")))

    # 动作
    action_names = sorted(set((a.get("name_cn") or "").strip() for a in acts_r if a.get("name_cn")))

    return {
        "entity_count": len(entity_names),
        "entity_names": entity_names,
        "entity_types": entity_types,
        "edge_count": len(edges),
        "edge_type_patterns": edge_types,
        "logic_count": len(logic_names),
        "logic_names": logic_names,
        "action_count": len(action_names),
        "action_names": action_names,
    }


# ─── 简易 LLM 路径 ─────────────────────────────────────
def run_simple_llm(run_idx: int, model_id: str, prompt_id: str) -> dict:
    files = [f for f in sorted(DOMAIN_FOLDER.glob("*")) if f.suffix.lower() in TEXT_EXT]
    r = s.post(f"{API}/api/v1/ontologies", json={
        "name": f"供应链-简易LLM-run{run_idx}-{int(time.time())}",
        "domain": "供应链", "build_mode": "simple_llm",
    })
    oid = r.json()["data"]["id"]
    for f in files:
        with open(f, "rb") as fh:
            s.post(f"{API}/api/v1/ontologies/{oid}/files", files={"file": (f.name, fh)})
    r = s.post(f"{API}/api/v1/ontologies/{oid}/execute", json={
        "prompt_id": prompt_id, "model_id": model_id, "model_name": "mock-extractor"})
    if not r.ok:
        return {"run": run_idx, "error": f"execute failed: {r.status_code}"}
    tid = r.json()["data"]["task_id"]
    status = "running"
    for _ in range(90):
        time.sleep(2)
        task = s.get(f"{API}/api/v1/ontologies/{oid}/execute/status",
                     params={"task_id": tid}).json().get("data", {})
        status = task.get("status")
        if status in ("success", "failed", "completed"):
            break
    snap = get_ontology_snapshot(oid)
    snap["run"] = run_idx
    snap["oid"] = oid
    snap["status"] = status
    return snap


# ─── Pipeline Mapping 路径 ───────────────────────────────
def run_pipeline_mapping(run_idx: int) -> dict:
    files = sorted(DOMAIN_FOLDER.glob("*"))
    conn_files = []
    for f in files:
        with open(f, "rb") as fh:
            r = s.post(f"{API}/api/v2/datasets/upload", files={"file": (f.name, fh)})
        j = r.json().get("data") or r.json()
        conn_files.append({"name": f.name, "dataset_id": j["id"], "kind": j.get("kind")})

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
        "name": f"供应链-管道-run{run_idx}-{int(time.time())}", "definition": definition})
    pid = (r.json().get("data") or r.json())["id"]

    res = s.post(f"{API}/api/v2/pipelines/{pid}/run-sync").json()
    outputs = ((res.get("stats") or {}).get("meta") or {}).get("outputs", [])

    r = s.post(f"{API}/api/v1/ontologies", json={
        "name": f"供应链-Mapping-run{run_idx}-{int(time.time())}",
        "domain": "供应链", "build_mode": "pipeline_mapping",
    })
    oid = r.json()["data"]["id"]
    for o in outputs:
        cid = o["curated_dataset_id"]
        s.post(f"{API}/api/v2/curated/{cid}/review", params={"action": "approve"})
        s.post(f"{API}/api/v2/ontologies/{oid}/mappings", json={
            "curated_dataset_id": cid,
            "entity_class": pascal(o["source_file"]),
            "field_mapping": {}})
    rb = s.post(f"{API}/api/v2/ontologies/{oid}/mappings/build-all")
    snap = get_ontology_snapshot(oid)
    snap["run"] = run_idx
    snap["oid"] = oid
    snap["curated_count"] = len(outputs)
    return snap


# ─── 比较函数 ──────────────────────────────────────────
def compare_snapshots(snapshots: list[dict], label: str) -> dict:
    print(f"\n{'='*60}")
    print(f"  {label} — {len(snapshots)} 次运行对比")
    print(f"{'='*60}")

    # 统计各次结果
    for snap in snapshots:
        r = snap.get("run", "?")
        print(f"  Run {r}: 实体={snap.get('entity_count',0)} "
              f"边={snap.get('edge_count',0)} "
              f"逻辑={snap.get('logic_count',0)} "
              f"动作={snap.get('action_count',0)}")

    differences = {}

    # 实体名称集合对比
    entity_sets = [frozenset(s.get("entity_names", [])) for s in snapshots]
    all_same_entities = len(set(entity_sets)) == 1
    if not all_same_entities:
        differences["entity_names"] = []
        ref = entity_sets[0]
        for i, es in enumerate(entity_sets[1:], 2):
            added = sorted(es - ref)
            removed = sorted(ref - es)
            if added or removed:
                differences["entity_names"].append({
                    "run": i, "added": added, "removed": removed
                })

    # 实体类型对比
    type_sets = [frozenset(s.get("entity_types", [])) for s in snapshots]
    all_same_types = len(set(type_sets)) == 1
    if not all_same_types:
        differences["entity_types"] = []
        ref = type_sets[0]
        for i, ts in enumerate(type_sets[1:], 2):
            added = sorted(ts - ref)
            removed = sorted(ref - ts)
            if added or removed:
                differences["entity_types"].append({
                    "run": i, "added": added, "removed": removed
                })

    # 边类型模式对比
    edge_sets = [frozenset(s.get("edge_type_patterns", [])) for s in snapshots]
    all_same_edges = len(set(edge_sets)) == 1
    if not all_same_edges:
        differences["edge_patterns"] = []
        ref = edge_sets[0]
        for i, es in enumerate(edge_sets[1:], 2):
            added = sorted(es - ref)
            removed = sorted(ref - es)
            if added or removed:
                differences["edge_patterns"].append({
                    "run": i, "added": added, "removed": removed
                })

    # 逻辑规则对比
    logic_sets = [frozenset(s.get("logic_names", [])) for s in snapshots]
    all_same_logic = len(set(logic_sets)) == 1
    if not all_same_logic:
        differences["logic_names"] = []
        ref = logic_sets[0]
        for i, ls in enumerate(logic_sets[1:], 2):
            added = sorted(ls - ref)
            removed = sorted(ref - ls)
            if added or removed:
                differences["logic_names"].append({
                    "run": i, "added": added, "removed": removed
                })

    # 动作对比
    action_sets = [frozenset(s.get("action_names", [])) for s in snapshots]
    all_same_actions = len(set(action_sets)) == 1
    if not all_same_actions:
        differences["action_names"] = []
        ref = action_sets[0]
        for i, as_ in enumerate(action_sets[1:], 2):
            added = sorted(as_ - ref)
            removed = sorted(ref - as_)
            if added or removed:
                differences["action_names"].append({
                    "run": i, "added": added, "removed": removed
                })

    # 数量一致性
    counts = [(s.get("entity_count"), s.get("edge_count"),
               s.get("logic_count"), s.get("action_count")) for s in snapshots]
    all_same_counts = len(set(counts)) == 1

    print(f"\n  数量一致: {'✅' if all_same_counts else '❌'}")
    print(f"  实体名称一致: {'✅' if all_same_entities else '❌'}")
    print(f"  实体类型一致: {'✅' if all_same_types else '❌'}")
    print(f"  边模式一致: {'✅' if all_same_edges else '❌'}")
    print(f"  逻辑规则一致: {'✅' if all_same_logic else '❌'}")
    print(f"  动作一致: {'✅' if all_same_actions else '❌'}")

    if differences:
        print(f"\n  ⚠ 差异详情:")
        for key, diffs in differences.items():
            print(f"    [{key}]:")
            for d in diffs:
                if d.get("added"):
                    print(f"      Run{d['run']} 多出: {d['added'][:5]}")
                if d.get("removed"):
                    print(f"      Run{d['run']} 缺少: {d['removed'][:5]}")
    else:
        print(f"\n  ✅ 所有 {len(snapshots)} 次运行结果完全一致")

    return differences


# ─── 主流程 ──────────────────────────────────────────────
if __name__ == "__main__":
    model_id, prompt_id = ensure_model_prompt()
    print(f"✅ 模型: {model_id[:8]}  提示词: {prompt_id[:8]}")

    print(f"\n{'='*60}")
    print(f"  测试数据: {DOMAIN_FOLDER}")
    print(f"  文件列表: {[f.name for f in sorted(DOMAIN_FOLDER.glob('*'))]}")
    print(f"  运行次数: {RUNS}")
    print(f"{'='*60}")

    # ── 简易 LLM 路径 × RUNS 次 ──
    print(f"\n▶ 开始简易 LLM 路径测试 ({RUNS} 次)...")
    simple_snapshots = []
    for i in range(1, RUNS + 1):
        print(f"  [Simple LLM] Run {i}/{RUNS}...", end=" ", flush=True)
        snap = run_simple_llm(i, model_id, prompt_id)
        simple_snapshots.append(snap)
        print(f"实体={snap.get('entity_count',0)} 边={snap.get('edge_count',0)} "
              f"逻辑={snap.get('logic_count',0)} 动作={snap.get('action_count',0)}")

    # ── Pipeline Mapping 路径 × RUNS 次 ──
    print(f"\n▶ 开始 Pipeline Mapping 路径测试 ({RUNS} 次)...")
    mapping_snapshots = []
    for i in range(1, RUNS + 1):
        print(f"  [Mapping] Run {i}/{RUNS}...", end=" ", flush=True)
        snap = run_pipeline_mapping(i)
        mapping_snapshots.append(snap)
        print(f"实体={snap.get('entity_count',0)} 边={snap.get('edge_count',0)} "
              f"逻辑={snap.get('logic_count',0)} 动作={snap.get('action_count',0)}")

    # ── 对比 ──
    simple_diffs = compare_snapshots(simple_snapshots, "简易 LLM 提取路径")
    mapping_diffs = compare_snapshots(mapping_snapshots, "Pipeline Mapping 路径")

    print(f"\n{'='*60}")
    print("  总结")
    print(f"{'='*60}")
    simple_ok = not simple_diffs
    mapping_ok = not mapping_diffs
    print(f"  简易 LLM 路径: {'✅ 完全一致' if simple_ok else '❌ 存在差异'}")
    print(f"  Pipeline Mapping 路径: {'✅ 完全一致' if mapping_ok else '❌ 存在差异'}")

    if not simple_ok or not mapping_ok:
        print("\n  ❌ 发现不一致，详见上方差异报告")
        sys.exit(1)
    else:
        print("\n  ✅ 两条路径均可重复")
        sys.exit(0)

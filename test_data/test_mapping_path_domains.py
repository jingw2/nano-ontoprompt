#!/usr/bin/env python3
"""Pipeline Mapping 路径 — 医疗 / 财务 域测试驱动

每域: 全部文件 → Pipeline (A/B/C) → 审批 → 映射 → build-all → 图谱拓扑。
"""
import sys
import time
from collections import Counter
from pathlib import Path

import requests

API = "http://localhost:8000"
TD = Path(__file__).parent

# 文件 → (entity_class, route, 主键)
DOMAINS = {
    "医疗": {
        "医疗/adverse_events.csv": ("AdverseEvent", "A", "事件ID"),
        "医疗/clinical_data.xlsx": ("Drug", "A", "药品名"),
        "医疗/followup_records.csv": ("FollowupRecord", "A", "__row_hash__"),
        "医疗/clinical_protocols.md": ("ClinicalProtocol", "C", "record_id"),
        "医疗/treatment_procedures.docx": ("TreatmentProcedure", "C", "record_id"),
        "医疗/drug_safety_report.pdf": ("DrugSafetyRule", "C", "record_id"),
        "医疗/clinical_review.pptx": ("ClinicalReviewItem", "C", "record_id"),
    },
    "财务": {
        "财务/cash_flow.csv": ("CashFlowRecord", "A", "__row_hash__"),
        "财务/expense_reports.csv": ("ExpenseReport", "A", "报销单号"),
        "财务/financial_data.xlsx": ("DeptBudget", "A", "__row_hash__"),
        "财务/financial_controls.md": ("FinancialControl", "C", "record_id"),
        "财务/month_end_close.docx": ("CloseProcedure", "C", "record_id"),
        "财务/audit_report.pdf": ("AuditRule", "C", "record_id"),
        "财务/cfo_review.pptx": ("CfoReviewItem", "C", "record_id"),
    },
}

s = requests.Session()
r = s.post(f"{API}/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
s.headers.update({"Authorization": f"Bearer {r.json()['data']['access_token']}"})
print("✅ 登录")

summary = []
for domain, files in DOMAINS.items():
    print(f"\n══ {domain} (Pipeline Mapping) ══")
    curated = {}
    for fname, (cls, route, pk) in files.items():
        p = TD / fname
        with open(p, "rb") as fh:
            r = s.post(f"{API}/api/v2/datasets/upload", files={"file": (p.name, fh)})
        did = (r.json().get("data") or r.json())["id"]
        body = {
            "name": f"{domain}-{int(time.time() * 1000) % 100000}-{p.name[:18]}",
            "source_dataset_id": did, "route": route,
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
        pid = (r.json().get("data") or r.json()).get("id")
        res = s.post(f"{API}/api/v2/pipelines/{pid}/run-sync").json()
        stats = res.get("stats") or {}
        cid = stats.get("curated_dataset_id")
        print(f"  {p.name:30s} route={route} {res.get('status')} rows={stats.get('rows_out')}")
        if cid:
            curated[fname] = (cls, pk, cid)
            s.post(f"{API}/api/v2/curated/{cid}/review", params={"action": "approve"})

    r = s.post(f"{API}/api/v1/ontologies", json={
        "name": f"{domain}-Mapping-{int(time.time())}", "domain": domain,
        "build_mode": "pipeline_mapping", "description": "Pipeline Mapping 路径测试",
    })
    oid = r.json()["data"]["id"]
    for fname, (cls, pk, cid) in curated.items():
        s.post(f"{API}/api/v2/ontologies/{oid}/mappings", json={
            "curated_dataset_id": cid, "entity_class": cls,
            "field_mapping": {"__primary_key__": pk},
        })
    r = s.post(f"{API}/api/v2/ontologies/{oid}/mappings/build-all")
    res = r.json()
    print(f"  build-all: {r.status_code} entities={res.get('total_entities')} "
          f"relations={res.get('total_relations')} logic={res.get('total_logic')} actions={res.get('total_actions')}")

    g = s.get(f"{API}/api/v2/ontologies/{oid}/graph?limit=1000").json()
    g = g.get("data", g)
    lbl = {n["id"]: (n.get("labels") or ["?"])[0] for n in g["nodes"]}
    pairs = Counter((lbl.get(e["source"], "?"), e["type"], lbl.get(e["target"], "?")) for e in g["edges"])
    print(f"  图谱: {len(g['nodes'])} 节点 {len(g['edges'])} 边")
    for k, v in pairs.most_common():
        print(f"    {k[0]} -[{k[1]}]-> {k[2]} × {v}")
    linked = {lbl.get(e["source"]) for e in g["edges"]} | {lbl.get(e["target"]) for e in g["edges"]}
    print(f"  孤立类型: {sorted(set(lbl.values()) - linked)}")
    summary.append((domain, oid, r.status_code, len(g["nodes"]), len(g["edges"]),
                    res.get("total_logic"), res.get("total_actions")))

print("\n══ Mapping 路径汇总 ══")
ok = True
for domain, oid, code, nodes, edges, logic, acts in summary:
    flag = "✅" if code == 200 and nodes > 0 else "❌"
    ok = ok and flag == "✅"
    print(f"{flag} {domain}: {oid[:8]} 节点={nodes} 边={edges} logic={logic} actions={acts}")
sys.exit(0 if ok else 1)

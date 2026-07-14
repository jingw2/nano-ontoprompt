#!/usr/bin/env python3
"""
最终 E2E 验证：供应链8文件 → Pipeline → Curated → Ontology
核心修复：FK检测+实体映射+Link Mapping → 网络状知识图谱
"""
import time, requests, os
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_API = "http://localhost:8000"
BASE_UI  = "http://localhost:5173"
TEST_DATA = Path(__file__).parent / "供应链"
SS_DIR   = Path(__file__).parent / "screenshots" / "final_e2e"
SS_DIR.mkdir(parents=True, exist_ok=True)
s = requests.Session()

def api(method, path, **kw):
    return s.request(method, f"{BASE_API}{path}", **kw)

def shot(page, name):
    page.screenshot(path=str(SS_DIR / name), full_page=False)
    print(f"  📸 {name}")

def main():
    print("═"*60)
    print("  E2E 最终验证")
    print("═"*60)

    for i in range(30):
        try:
            r = s.post(f"{BASE_API}/api/v1/auth/login", json={"username":"admin","password":"admin123"})
            if r.ok: break
        except: pass
        time.sleep(1)
    s.headers.update({"Authorization": f"Bearer {r.json()['data']['access_token']}"})
    print("✅ 登录")
    ts = int(time.time())

    # ═══ 1. 上传 ═══
    print("\n── 1. 上传 ──")
    fm = {}
    for f in sorted(TEST_DATA.glob("*")):
        with open(f, "rb") as fh:
            r = api("POST", "/api/v2/datasets/upload", files={"file": (f.name, fh, "application/octet-stream")})
        if r.ok:
            d = r.json()
            did = d.get("data", d).get("id") if isinstance(d, dict) else None
            fm[f.name] = did
            print(f"  ✅ {f.name:35s} → {did[:8] if did else '?'}")
        time.sleep(0.15)

    # ═══ 2. Pipeline (含 supplier_database) ═══
    print("\n── 2. Pipeline ──")
    curated = {}
    for fname, route in [
        ("inventory_transactions.csv","A"), ("logistics_performance.csv","A"),
        ("supplier_database.xlsx","A"), ("supplier_orders.json","B"),
    ]:
        if fname not in fm: continue
        r = api("POST", "/api/v2/pipelines", json={
            "name": f"E2E-{fname.split('.')[0]}-{ts}", "domain": "供应链",
            "source_dataset_id": fm[fname], "route": route,
            "definition":{
                "schema_version":"2.0",
                "nodes":[
                    {"id":"conn","type":"connector","label":"数据源","position":{"x":50,"y":200},
                     "config":{"source_type":"file","files":[{"name":fname}]}},
                    {"id":"stor","type":"storage","label":"存储","position":{"x":250,"y":200}},
                    {"id":"xfm","type":"transform","label":"转换","position":{"x":450,"y":200},
                     "config":{"path":"structured" if route=="A" else "semi_structured"}},
                    {"id":"out","type":"output","label":"输出","position":{"x":680,"y":200},
                     "config":{"dataset_type":"curated_dataset","primary_key":["id"]}},
                ],
                "edges":[{"id":"e1","source":"conn","target":"stor"},
                         {"id":"e2","source":"stor","target":"xfm"},
                         {"id":"e3","source":"xfm","target":"out"}],
            },
        })
        if r.ok:
            pid = r.json()["id"]
            r2 = api("POST", f"/api/v2/pipelines/{pid}/run-sync")
            if r2.ok:
                res = r2.json()
                cid = res.get("stats", {}).get("curated_dataset_id")
                if cid: curated[fname] = cid
                print(f"  ✅ {fname:35s} run={res['status']} rows={res.get('stats',{}).get('rows_in',0)}")
            else:
                print(f"  ⚠️ {fname}: run fail {r2.text[:60]}")

    # ═══ 3. 审批 ═══
    print("\n── 3. 审批 ──")
    for cid in curated.values():
        api("POST", f"/api/v2/curated/{cid}/review?action=approve")
    print(f"  ✅ {len(curated)} 个 Curated 已审批")

    # ═══ 4. Ontology + 智能 Entity Type 命名 ═══
    print(f"\n── 4. Ontology ──")
    entity_names = {
        "inventory_transactions.csv": "InventoryTransaction",
        "logistics_performance.csv": "LogisticsRecord",
        "supplier_database.xlsx": "Supplier",
        "supplier_orders.json": "PurchaseOrder",
    }
    r = api("POST", "/api/v1/ontologies", json={
        "name": f"供应链知识图谱-E2E-{ts}", "domain": "供应链",
        "description": "E2E 全流程验证 - 含关系网络", "build_mode": "pipeline_mapping",
    })
    d = r.json()
    onto_id = d.get("data", d).get("id") if isinstance(d, dict) else d.get("id")
    print(f"  ✅ Ontology: {onto_id[:8] if onto_id else 'N/A'}")

    if onto_id:
        dsid_to_etype = {}
        for fname, cid in curated.items():
            etype = entity_names.get(fname, f"E_{fname.split('.')[0]}")
            r2 = api("POST", f"/api/v2/ontologies/{onto_id}/mappings", json={
                "curated_dataset_id": cid, "entity_class": etype,
                "field_mapping": {"__primary_key__": "id"},
            })
            mid = r2.json().get("mapping_id", "") if r2.ok else ""
            if mid:
                api("POST", f"/api/v2/ontologies/{onto_id}/mappings/{mid}/apply-from-dataset")
                dsid_to_etype[cid] = etype
                print(f"  ✅ {etype:25s} mapped to {fname}")

        # Build-all
        r = api("POST", f"/api/v2/ontologies/{onto_id}/mappings/build-all")
        res = r.json()
        print(f"  build-all: entities={res.get('total_entities')} relations={res.get('total_relations')}")

        # ═══ 创建 Link Mappings 构建知识图谱网络 ═══
        print("\n── 5. 创建 Link Mappings ──")
        # Logistics.供应商 → Supplier.供应商ID (跨表关系)
        logistics_ds = [cid for fn, cid in curated.items() if "logistics" in fn]
        supplier_ds = [cid for fn, cid in curated.items() if "supplier_database" in fn]
        orders_ds = [cid for fn, cid in curated.items() if "supplier_orders" in fn]
        inventory_ds = [cid for fn, cid in curated.items() if "inventory" in fn]

        if logistics_ds and supplier_ds:
            r = api("POST", f"/api/v2/ontologies/{onto_id}/link-mappings", json={
                "src_dataset_id": logistics_ds[0], "tgt_dataset_id": supplier_ds[0],
                "relation_type": "HAS_SUPPLIER", "src_key": "供应商", "tgt_key": "供应商名称",
            })
            if r.ok: print(f"  ✅ Logistics -[HAS_SUPPLIER]-> Supplier")

        if orders_ds and supplier_ds:
            r = api("POST", f"/api/v2/ontologies/{onto_id}/link-mappings", json={
                "src_dataset_id": orders_ds[0], "tgt_dataset_id": supplier_ds[0],
                "relation_type": "HAS_SUPPLIER", "src_key": "supplier.name", "tgt_key": "供应商名称",
            })
            if r.ok: print(f"  ✅ PurchaseOrder -[HAS_SUPPLIER]-> Supplier")

        if logistics_ds and inventory_ds:
            r = api("POST", f"/api/v2/ontologies/{onto_id}/link-mappings", json={
                "src_dataset_id": logistics_ds[0], "tgt_dataset_id": inventory_ds[0],
                "relation_type": "USES_MATERIAL", "src_key": "运单号", "tgt_key": "物料编码",
            })
            if r.ok: print(f"  ✅ Logistics -[USES_MATERIAL]-> Inventory")

        # 重新 Build-all 以应用 Link Mappings
        r = api("POST", f"/api/v2/ontologies/{onto_id}/mappings/build-all")
        res2 = r.json()
        print(f"  build-all (after links): entities={res2.get('total_entities')} relations={res2.get('total_relations')}")

        # Logic + Action Discovery
        ld = api("POST", f"/api/v2/ontologies/{onto_id}/logic/discover").json()
        ad = api("POST", f"/api/v2/ontologies/{onto_id}/actions/discover").json()
        print(f"\n── 6. Logic & Actions ──")
        print(f"  Logic: v1={ld.get('total_v1',0)} v2={ld.get('total_v2',0)}")
        print(f"  Actions: v1={ad.get('total_v1',0)} v2={ad.get('total_v2',0)}")

        # ── SUCCESS CRITERIA ──
        r = api("GET", f"/api/v1/ontologies/{onto_id}/entities")
        ents = r.json() if r.ok else {}
        if isinstance(ents, dict):
            d2 = ents.get("data", ents)
            ents_list = d2.get("items", []) if isinstance(d2, dict) else d2
        else: ents_list = ents if isinstance(ents, list) else []

        r = api("GET", f"/api/v1/ontologies/{onto_id}/logic")
        logic_items = r.json().get("data", []) if r.ok else []
        r = api("GET", f"/api/v1/ontologies/{onto_id}/actions")
        acts = r.json().get("data", []) if r.ok else []
        r = api("GET", f"/api/v2/ontologies/{onto_id}/graph?limit=300")
        graph = r.json() if r.ok else {}
        gnodes = graph.get("nodes", graph.get("data", {}).get("nodes", []))
        gedges = graph.get("edges", graph.get("data", {}).get("edges", []))

        print(f"\n{'═'*50}")
        print(f"  SUCCESS CRITERIA")
        print(f"{'═'*50}")
        checks = [
            ("Entities > 0", len(ents_list) > 0),
            ("Logic > 0", len(logic_items) > 0),
            ("Actions > 0", len(acts) > 0),
            ("Graph nodes > 0", len(gnodes) > 0 if isinstance(gnodes, list) else False),
            ("Graph edges > 0", len(gedges) > 0 if isinstance(gedges, list) else False),
        ]
        for label, ok in checks:
            print(f"  {'✅' if ok else '❌'} {label}")
        all_ok = all(ok for _, ok in checks)
        print(f"\n  {'ALL PASSED ✅' if all_ok else 'SOME FAILED ❌'}")
        print(f"{'═'*50}")

        print(f"  Details:")
        print(f"  Entities: {len(ents_list)}  Logic: {len(logic_items)}  Actions: {len(acts)}")
        print(f"  Graph: {len(gnodes)} nodes, {len(gedges)} edges")
        for lr in logic_items:
            print(f"    Logic: {lr.get('name_cn','?')} (enabled={lr.get('enabled',True)})")
        for a in acts:
            print(f"    Action: {a.get('name_cn','?')} (enabled={a.get('enabled',True)})")

    # ═══ Playwright ═══
    print(f"\n── 7. Playwright ──")
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=False, slow_mo=300, args=["--window-size=1440,900"])
        ctx = br.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.goto(f"{BASE_UI}/login", wait_until="networkidle")
        page.locator("input").first.fill("admin")
        page.locator("input[type='password']").first.fill("admin123")
        page.locator("button[type='submit']").first.click()
        page.wait_for_url("**/overview", timeout=15000)
        time.sleep(1.5); shot(page, "01_overview.png")

        page.goto(f"{BASE_UI}/pipelines", wait_until="networkidle")
        time.sleep(1.5); shot(page, "02_pipeline_list.png")

        row = page.locator("table tbody tr").first
        if row.count() > 0:
            row.locator("button").first.click()
            time.sleep(3); shot(page, "03_pipeline_builder.png")
            c = page.locator(".react-flow__node-connector").first
            if c.count() > 0: c.click(); time.sleep(1); shot(page, "04_connector_detail.png")
            o = page.locator(".react-flow__node-output").first
            if o.count() > 0: o.click(); time.sleep(1); shot(page, "05_output_detail.png")

        if onto_id:
            page.goto(f"{BASE_UI}/ontologies/{onto_id}", wait_until="networkidle")
            time.sleep(2); shot(page, "06_ontology_info.png")
            for t, n in [("Graph","07_graph"),("Entities","08_entities"),("Logic","09_logic"),("Actions","10_actions")]:
                tb = page.locator(f"button:has-text('{t}')")
                if tb.count() > 0: tb.first.click(); time.sleep(2); shot(page, f"{n}.png")

        br.close()
    print(f"  截图: {len(list(SS_DIR.glob('*.png')))} 张")

if __name__ == "__main__":
    main()

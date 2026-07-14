#!/usr/bin/env python3
"""最终 E2E 验证测试：供应链 8 文件 → Pipeline → Curated → Ontology → 验证"""
import time, requests, os
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_API = "http://localhost:8000"
BASE_UI  = "http://localhost:5173"
TEST_DATA = Path(__file__).parent / "供应链"
SS_DIR   = Path(__file__).parent / "screenshots" / "e2e_final"
SS_DIR.mkdir(parents=True, exist_ok=True)
session = requests.Session()

def api(method, path, **kw):
    return session.request(method, f"{BASE_API}{path}", **kw)

def shot(page, name):
    page.screenshot(path=str(SS_DIR / name), full_page=False)
    print(f"  📸 {name}")

def main():
    print("═══ E2E 最终验证 ═══")

    for i in range(30):
        try:
            r = api("POST", "/api/v1/auth/login", json={"username":"admin","password":"admin123"})
            if r.ok: break
        except: pass
        time.sleep(1)
    t = r.json()["data"]["access_token"]
    session.headers.update({"Authorization": f"Bearer {t}"})
    print("✅ 登录成功")
    ts = int(time.time())

    # ═══ 1. 上传文件 ═══
    print("\n── 1. 上传文件 ──")
    files_map = {}
    for f in sorted(TEST_DATA.glob("*")):
        with open(f, "rb") as fh:
            r = api("POST", "/api/v2/datasets/upload", files={"file": (f.name, fh, "application/octet-stream")})
        if r.ok:
            d = r.json()
            did = d.get("data", d).get("id") if isinstance(d, dict) else None
            files_map[f.name] = did
            print(f"  ✅ {f.name:35s} → {did[:8] if did else 'N/A'}")
        time.sleep(0.2)

    # ═══ 2. Pipeline ═══
    print("\n── 2. Pipeline ──")
    pipelines = {}
    for fname, route in [("inventory_transactions.csv","A"),("logistics_performance.csv","A"),
                          ("supplier_orders.json","B")]:
        if fname not in files_map: continue
        name = fname.rsplit(".",1)[0]
        r = api("POST", "/api/v2/pipelines", json={
            "name": f"{name}-{ts}", "domain": "供应链",
            "source_dataset_id": files_map[fname], "route": route,
            "definition":{"schema_version":"2.0",
                "nodes":[
                    {"id":"conn_1","type":"connector","label":"数据源","position":{"x":50,"y":200},
                     "config":{"source_type":"file","files":[{"name":fname,"size":999}]}},
                    {"id":"stor_1","type":"storage","label":"存储","position":{"x":250,"y":200}},
                    {"id":"xfm_1","type":"transform","label":"清洗","position":{"x":450,"y":200},
                     "config":{"path":"structured" if route=="A" else "semi_structured"}},
                    {"id":"out_1","type":"output","label":"输出","position":{"x":680,"y":200},
                     "config":{"dataset_type":"curated_dataset","primary_key":["id"]}},
                ],
                "edges":[{"id":"e1","source":"conn_1","target":"stor_1"},{"id":"e2","source":"stor_1","target":"xfm_1"},{"id":"e3","source":"xfm_1","target":"out_1"}]},
        })
        if r.ok:
            pid = r.json()["id"]
            r2 = api("POST", f"/api/v2/pipelines/{pid}/run-sync")
            if r2.ok:
                res = r2.json()
                cid = res.get("stats",{}).get("curated_dataset_id")
                pipelines[fname] = {"pid":pid,"cid":cid}
                print(f"  ✅ {name:30s} status={res['status']} rows_in={res.get('stats',{}).get('rows_in')}")
            else:
                print(f"  ⚠️ {name}: run fail")
        else:
            print(f"  ❌ {name}: create fail")

    # ═══ 3. 验证节点详情 ═══
    print("\n── 3. 验证节点 ──")
    for fname, info in pipelines.items():
        pid, cid = info["pid"], info["cid"]
        r = api("GET", f"/api/v2/pipelines/{pid}")
        if r.ok:
            nodes = (r.json().get("definition") or {}).get("nodes", [])
            conn_files = [n.get("config",{}).get("files",[]) for n in nodes if n["type"]=="connector"]
            if conn_files:
                print(f"  ✅ {fname}: Connector 文件={[f['name'] for f in conn_files[0]]}")
            if cid:
                r2 = api("GET", f"/api/v2/datasets/{cid}/versions")
                if r2.ok and r2.json():
                    vno = r2.json()[0]["version_no"]
                    r3 = api("GET", f"/api/v2/datasets/{cid}/versions/{vno}/preview?limit=3")
                    if r3.ok and r3.json():
                        print(f"  ✅ {fname}: Output 预览 {len(r3.json())} 行")

    # ═══ 4. Ontology ═══
    print("\n── 4. Ontology ──")
    r = api("GET", "/api/v2/curated")
    items = r.json() if r.ok else []
    items = items if isinstance(items, list) else []
    for c in items:
        if c.get("status") == "pending_review":
            api("POST", f"/api/v2/curated/{c['id']}/review?action=approve")

    r = api("POST", "/api/v1/ontologies", json={
        "name": f"供应链知识图谱-{ts}", "domain": "供应链",
        "description": "E2E 验证", "build_mode": "pipeline_mapping",
    })
    onto_id = r.json().get("data",r.json()).get("id") if r.ok and isinstance(r.json(), dict) else None
    print(f"  ✅ Ontology: {onto_id[:8] if onto_id else 'N/A'}")

    if onto_id:
        r = api("GET", "/api/v2/curated")
        items = r.json() if r.ok else []
        items = items if isinstance(items, list) else []
        for c in items[:4]:
            ename = c.get("name","").replace(" ","_")[:20]
            r2 = api("POST", f"/api/v2/ontologies/{onto_id}/mappings", json={
                "curated_dataset_id": c["id"], "entity_class": f"E_{ename}",
                "field_mapping": {"__primary_key__": "id"},
            })
            mid = r2.json().get("mapping_id","") if r2.ok else ""
            if mid:
                api("POST", f"/api/v2/ontologies/{onto_id}/mappings/{mid}/apply-from-dataset")
                print(f"  ✅ {ename:30s} mapped")

        api("POST", f"/api/v2/ontologies/{onto_id}/mappings/build-all")
        ld = api("POST", f"/api/v2/ontologies/{onto_id}/logic/discover").json()
        ad = api("POST", f"/api/v2/ontologies/{onto_id}/actions/discover").json()
        print(f"  ✅ Logic: v1={ld.get('total_v1',0)} v2={ld.get('total_v2',0)}")
        print(f"  ✅ Actions: v1={ad.get('total_v1',0)} v2={ad.get('total_v2',0)}")

        # 验证 Entities
        r = api("GET", f"/api/v1/ontologies/{onto_id}/entities")
        ents = r.json() if r.ok else {}
        if isinstance(ents, dict):
            d = ents.get("data", ents)
            ents_list = d.get("items", []) if isinstance(d, dict) else d
        else: ents_list = ents if isinstance(ents, list) else []
        print(f"  ✅ Entities: {len(ents_list)}")

        # 验证 Logic
        r = api("GET", f"/api/v1/ontologies/{onto_id}/logic")
        rules = r.json().get("data",[]) if r.ok else []
        print(f"  ✅ Logic: {len(rules)} 条")
        for rule in rules:
            print(f"    - {rule.get('name_cn','?')}")

        # 验证 Actions
        r = api("GET", f"/api/v1/ontologies/{onto_id}/actions")
        acts = r.json().get("data",[]) if r.ok else []
        print(f"  ✅ Actions: {len(acts)} 条")
        for a in acts:
            print(f"    - {a.get('name_cn','?')}")

        # 验证 Graph
        r = api("GET", f"/api/v2/ontologies/{onto_id}/graph?limit=200")
        graph = r.json() if r.ok else {}
        gnodes = graph.get("nodes", graph.get("data",{}).get("nodes",[]))
        gedges = graph.get("edges", graph.get("data",{}).get("edges",[]))
        print(f"  ✅ Graph: {len(gnodes)} nodes, {len(gedges)} edges")

    # ═══ 5. Playwright ═══
    print("\n── 5. Playwright ──")
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=False, slow_mo=300, args=["--window-size=1440,900"])
        ctx = br.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()

        page.goto(f"{BASE_UI}/login", wait_until="networkidle")
        page.locator("input").first.fill("admin")
        page.locator("input[type='password']").first.fill("admin123")
        page.locator("button[type='submit']").first.click()
        page.wait_for_url("**/overview", timeout=15000)
        time.sleep(1.5)
        shot(page, "01_overview.png")

        page.goto(f"{BASE_UI}/pipelines", wait_until="networkidle")
        time.sleep(1.5)
        shot(page, "02_pipeline_list.png")

        row = page.locator('table tbody tr').first
        if row.count() > 0:
            row.locator('button').first.click()
            time.sleep(3)
            shot(page, "03_pipeline_builder.png")

            cn = page.locator('.react-flow__node-connector').first
            if cn.count() > 0: cn.click(); time.sleep(1.5); shot(page, "04_connector_detail.png")
            on = page.locator('.react-flow__node-output').first
            if on.count() > 0: on.click(); time.sleep(1.5); shot(page, "05_output_detail.png")

        if onto_id:
            page.goto(f"{BASE_UI}/ontologies/{onto_id}", wait_until="networkidle")
            time.sleep(2); shot(page, "06_ontology_info.png")
            for t, n in [("Graph","07_graph"),("Entities","08_entities"),("Logic","09_logic"),("Actions","10_actions")]:
                tb = page.locator(f"button:has-text('{t}')")
                if tb.count() > 0: tb.first.click(); time.sleep(2); shot(page, f"{n}.png")

        br.close()

    print(f"\n{'═'*50}")
    print(f"✅ 完成")
    print(f"  截图: {len(list(SS_DIR.glob('*.png')))} 张")
    print(f"  Entities: {len(ents_list)}")
    print(f"  Logic: {len(rules)} | Actions: {len(acts)}")
    print(f"  Graph: {len(gnodes)} nodes, {len(gedges)} edges")
    print(f"  Ontology: {onto_id[:8] if onto_id else 'N/A'}")
    print(f"{'═'*50}")

if __name__ == "__main__":
    main()

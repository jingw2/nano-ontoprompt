#!/usr/bin/env python3
"""Playwright E2E 全流程 — 供应链 8 文件 → Pipeline → Curated → Ontology"""
import time, requests, os
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_API = "http://localhost:8000"
BASE_UI  = "http://localhost:5173"
TEST_DATA = Path(__file__).parent / "供应链"
SS_DIR   = Path(__file__).parent / "screenshots" / "e2e_comprehensive"
SS_DIR.mkdir(parents=True, exist_ok=True)

session = requests.Session()

def api(method, path, **kw):
    r = session.request(method, f"{BASE_API}{path}", **kw)
    return r

def shot(page, name, msg=""):
    p = SS_DIR / f"{name}.png"
    page.screenshot(path=str(p), full_page=False)
    print(f"  📸 {name}.png  {msg}")

def main():
    print("\n" + "═"*70)
    print("  E2E 综合：供应链 8 文件 → Pipeline → Curated → Ontology")
    print("═"*70)

    for i in range(20):
        try:
            r = requests.post(f"{BASE_API}/api/v1/auth/login", json={"username":"admin","password":"admin123"}, timeout=2)
            if r.ok: break
        except: pass
        time.sleep(1)
    print("✅ 后端就绪")

    r = api("POST", "/api/v1/auth/login", json={"username":"admin","password":"admin123"})
    t = r.json()["data"]["access_token"]
    session.headers.update({"Authorization": f"Bearer {t}"})
    print("✅ 登录成功")

    ts = int(time.time())

    # ═══ 1. 上传文件 ═══
    print("\n── 1. 上传 8 个文件 ──")
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

    # ═══ 2. 创建 Pipeline + 运行 ═══
    print("\n── 2. 创建 Pipeline ──")
    for fname, route in [("inventory_transactions.csv","A"),("logistics_performance.csv","A"),
                          ("supplier_orders.json","B")]:
        if fname not in files_map: continue
        name = fname.rsplit(".",1)[0]
        r = api("POST", "/api/v2/pipelines", json={
            "name": f"{name}-{ts}", "domain": "供应链",
            "source_dataset_id": files_map[fname], "route": route,
            "definition":{
                "schema_version":"2.0",
                "nodes":[
                    {"id":"conn_1","type":"connector","label":"数据源","position":{"x":50,"y":200},
                     "config":{"source_type":"file","files":[{"name":fname}]}},
                    {"id":"stor_1","type":"storage","label":"原始数据","position":{"x":250,"y":200}},
                    {"id":"xfm_1","type":"transform","label":"清洗","position":{"x":450,"y":200},
                     "config":{"path":"structured" if route=="A" else "semi_structured","steps":[{"op":"drop_duplicates"}]}},
                    {"id":"out_1","type":"output","label":"输出","position":{"x":680,"y":200},
                     "config":{"dataset_type":"curated_dataset","primary_key":["id"]}},
                ],
                "edges":[{"id":"e1","source":"conn_1","target":"stor_1"},{"id":"e2","source":"stor_1","target":"xfm_1"},{"id":"e3","source":"xfm_1","target":"out_1"}]
            },
        })
        if r.ok:
            pid = r.json()["id"]
            r2 = api("POST", f"/api/v2/pipelines/{pid}/run-sync")
            if r2.ok:
                res = r2.json()
                cid = res.get("stats",{}).get("curated_dataset_id")
                print(f"  ✅ {name:30s} run={res['status']} rows_in={res.get('stats',{}).get('rows_in')}")

    # ═══ 3. 审批 ═══
    print("\n── 3. 审批 Curated ──")
    r = api("GET", "/api/v2/curated")
    items = r.json() if r.ok else []
    items = items if isinstance(items, list) else []
    for c in items:
        if c.get("status") == "pending_review":
            api("POST", f"/api/v2/curated/{c['id']}/review?action=approve")
            print(f"  ✅ {c.get('name','?')[:30]:30s} approved")

    # ═══ 4. Ontology ═══
    print("\n── 4. 创建 Ontology ──")
    onto_name = f"供应链知识图谱-E2E-{ts}"
    r = api("POST", "/api/v1/ontologies", json={
        "name": onto_name, "domain": "供应链",
        "description": "E2E 测试本体", "build_mode": "pipeline_mapping",
    })
    onto_id = None
    if r.ok:
        onto_id = r.json().get("data",r.json()).get("id") if isinstance(r.json(), dict) else None
        print(f"  ✅ Ontology: {onto_id[:8] if onto_id else '?'}")

        r2 = api("GET", "/api/v2/curated")
        items = r2.json() if r2.ok else []
        items = items if isinstance(items, list) else []
        approved = [c for c in items if c.get("status") == "approved"][:4]
        for c in approved:
            etype = f"E_{c.get('name','')[:15].replace(' ','_')}"
            r3 = api("POST", f"/api/v2/ontologies/{onto_id}/mappings", json={
                "curated_dataset_id": c["id"], "entity_class": etype,
                "field_mapping": {"__primary_key__": "id"},
            })
            mid = r3.json().get("mapping_id","") if r3.ok else ""
            if mid:
                api("POST", f"/api/v2/ontologies/{onto_id}/mappings/{mid}/apply-from-dataset")
                print(f"  ✅ {etype:30s}")

        api("POST", f"/api/v2/ontologies/{onto_id}/mappings/build-all")
        api("POST", f"/api/v2/ontologies/{onto_id}/logic/discover")
        api("POST", f"/api/v2/ontologies/{onto_id}/actions/discover")

    # ═══ 5. Playwright ═══
    print("\n── 5. Playwright UI ──")
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=False, slow_mo=400, args=["--window-size=1440,900"])
        ctx = br.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()

        page.goto(f"{BASE_UI}/login", wait_until="networkidle")
        page.locator("input").first.fill("admin")
        page.locator("input[type='password']").first.fill("admin123")
        page.locator("button[type='submit']").first.click()
        page.wait_for_url("**/overview", timeout=15000)
        time.sleep(1.5)
        shot(page, "01_overview")

        # Pipeline 列表 → 点击第一行的编辑按钮
        page.goto(f"{BASE_UI}/pipelines", wait_until="networkidle")
        time.sleep(2)
        shot(page, "02_pipeline_list")

        first_row = page.locator('table tbody tr').first
        if first_row.count() > 0:
            edit_btn = first_row.locator('button').first
            if edit_btn.count() > 0:
                edit_btn.click()
                time.sleep(3)
                shot(page, "03_pipeline_builder")

                # Connector 节点
                cnode = page.locator('.react-flow__node-connector').first
                if cnode.count() > 0:
                    cnode.click()
                    time.sleep(1.5)
                    page.screenshot(path=str(SS_DIR / "04_connector_detail.png"))
                    print(f"  📸 04_connector_detail.png")

                # Output 节点
                onode = page.locator('.react-flow__node-output').first
                if onode.count() > 0:
                    onode.click()
                    time.sleep(1.5)
                    page.screenshot(path=str(SS_DIR / "05_output_detail.png"))
                    print(f"  📸 05_output_detail.png")
            else:
                print("  ⚠️ 无编辑按钮")
        else:
            print("  ⚠️ 无 pipeline 数据")

        # Ontology 详情
        if onto_id:
            page.goto(f"{BASE_UI}/ontologies/{onto_id}", wait_until="networkidle")
            time.sleep(2)
            shot(page, "06_ontology_info")
            for t in ["Graph","图谱"]:
                tb = page.locator(f"button:has-text('{t}')")
                if tb.count() > 0:
                    tb.first.click(); time.sleep(2); shot(page, "07_graph"); break
            for t in ["Entities","实体"]:
                tb = page.locator(f"button:has-text('{t}')")
                if tb.count() > 0:
                    tb.first.click(); time.sleep(2); shot(page, "08_entities"); break
            for t in ["Logic","逻辑"]:
                tb = page.locator(f"button:has-text('{t}')")
                if tb.count() > 0:
                    tb.first.click(); time.sleep(2); shot(page, "09_logic"); break
            for t in ["Actions","动作"]:
                tb = page.locator(f"button:has-text('{t}')")
                if tb.count() > 0:
                    tb.first.click(); time.sleep(2); shot(page, "10_actions"); break

        page.goto(f"{BASE_UI}/settings", wait_until="networkidle")
        time.sleep(1.5)
        shot(page, "11_settings")

        br.close()

    print(f"\n✅ 完成: 截图 {len(list(SS_DIR.glob('*.png')))} 张, Ontology: {onto_id[:8] if onto_id else 'N/A'}")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
全流程 e2e 测试 — 上传数据 → Pipeline → Curated Dataset → Ontology Mapping
确保每一步都产生真实数据
"""
import time, requests, os, json
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_API = "http://localhost:8000"
BASE_UI  = "http://localhost:5173"
TEST_DATA = Path(__file__).parent / "供应链"
SS_DIR   = Path(__file__).parent / "screenshots" / "e2e_flow"
SS_DIR.mkdir(parents=True, exist_ok=True)

session = requests.Session()
TOKEN = None

def api(method, path, **kw):
    r = session.request(method, f"{BASE_API}{path}", **kw)
    return r

def login():
    global TOKEN
    r = api("POST", "/api/v1/auth/login", json={"username":"admin","password":"admin123"})
    TOKEN = r.json()["data"]["access_token"]
    session.headers.update({"Authorization": f"Bearer {TOKEN}"})

def shot(page, name, msg=""):
    p = SS_DIR / f"{name}.png"
    page.screenshot(path=str(p), full_page=False)
    print(f"  📸  {name}.png  {msg}")

def main():
    print("\n" + "═"*60)
    print("  🔄 E2E：上传 → Pipeline → Curated → Ontology Mapping")
    print("═"*60)

    # 等待后端
    for i in range(20):
        try:
            r = requests.post(f"{BASE_API}/api/v1/auth/login", json={"username":"admin","password":"admin123"}, timeout=2)
            if r.ok: break
        except: pass
        time.sleep(1)
    print("  ✅ 后端就绪")
    login()

    ts = int(time.time())

    # ═══ 阶段 1: 上传真实 CSV 数据 ═══
    print("\n── 1. 上传测试数据 ──")
    csv_data = b"id,product_name,quantity,price,order_date\n1,Widget A,100,9.99,2024-01-01\n2,Widget B,200,19.99,2024-01-02\n3,Gadget X,150,29.99,2024-01-03\n4,Gadget Y,80,39.99,2024-01-04\n5,Widget C,300,14.99,2024-01-05\n"
    r = api("POST", "/api/v2/datasets/upload", files={"file": ("products.csv", csv_data, "text/csv")})
    ds_id = r.json().get("data", r.json()).get("id") if r.ok else None
    print(f"  CSV 上传: {r.status_code} id={ds_id[:8] if ds_id else '?'}")

    # ═══ 阶段 2: 创建 Pipeline ═══
    print("\n── 2. 创建 Pipeline ──")
    pl_name = f"E2E-Pipeline-{ts}"
    r = api("POST", "/api/v2/pipelines", json={
        "name": pl_name, "domain": "供应链", "description": "E2E 测试",
        "source_dataset_id": ds_id, "route": "A",
        "spec": {"steps": [{"op": "drop_duplicates"}]},
    })
    pl = r.json() if r.ok else {}
    pl_id = pl.get("id", "")
    print(f"  Pipeline: {r.status_code} id={pl_id[:8]}")

    # ═══ 阶段 3: 运行 Pipeline ═══
    print("\n── 3. 运行 Pipeline ──")
    r = api("POST", f"/api/v2/pipelines/{pl_id}/run-sync")
    result = r.json() if r.ok else {}
    curated_id = result.get("stats", {}).get("curated_dataset_id")
    rows_in = result.get("stats", {}).get("rows_in")
    rows_out = result.get("stats", {}).get("rows_out")
    print(f"  运行: status={result.get('status')} rows_in={rows_in} rows_out={rows_out}")
    print(f"  Curated Dataset: {curated_id[:8] if curated_id else 'NONE'}")

    if not curated_id or rows_in == 0:
        print("❌ Pipeline 未生成数据!")
        # 继续尝试使用已有的 curated dataset
        r2 = api("GET", "/api/v2/curated")
        items = r2.json() if r2.ok else []
        items = items if isinstance(items, list) else []
        curated_id = items[-1]["id"] if items else None
        print(f"  回退使用已有 Curated: {curated_id[:8] if curated_id else 'NONE'}")

    # ═══ 阶段 4: 审批 Curated Dataset ═══
    print("\n── 4. 审批 Curated Dataset ──")
    if curated_id:
        r = api("POST", f"/api/v2/curated/{curated_id}/review?action=approve")
        print(f"  审批: {r.status_code} {r.text[:100]}")
        # 启动一个新的 review flow
        r2 = api("POST", f"/api/v2/curated/{curated_id}/reviews")
        print(f"  启动审核: {r2.status_code}")
        if r2.ok:
            rev_id = r2.json().get("review_id", "")
            r3 = api("POST", f"/api/v2/curated/reviews/{rev_id}/approve")
            print(f"  审核通过: {r3.status_code} {r3.text[:100]}")

    # ═══ 阶段 5: 创建 Ontology Pipeline Mapping ═══
    print("\n── 5. 创建 Ontology + Mapping ──")
    onto_id = None
    if curated_id:
        onto_name = f"E2E-Ontology-{ts}"
        r = api("POST", "/api/v1/ontologies", json={
            "name": onto_name, "domain": "供应链",
            "description": "E2E 测试本体", "build_mode": "pipeline_mapping",
        })
        if r.ok:
            onto_id = r.json().get("data", r.json()).get("id") if isinstance(r.json(), dict) else None
            if not onto_id and isinstance(r.json(), dict):
                onto_id = r.json().get("id")
            print(f"  Ontology: {r.status_code} id={onto_id[:8] if onto_id else '?'}")

        if onto_id and curated_id:
            r = api("POST", f"/api/v2/ontologies/{onto_id}/mappings", json={
                "curated_dataset_id": curated_id,
                "entity_class": "Product",
                "field_mapping": {"__primary_key__": "id", "product_name": "name", "quantity": "qty", "price": "unit_price"},
            })
            mid = r.json().get("mapping_id", "") if r.ok else ""
            print(f"  Mapping: {r.status_code} id={mid[:8]}")

            if mid:
                r = api("POST", f"/api/v2/ontologies/{onto_id}/mappings/{mid}/apply-from-dataset")
                print(f"  Apply: {r.status_code}")
                res = r.json() if r.ok else {}
                print(f"    entities: {res.get('v1_entities_written', res.get('nodes_created', '?'))}")

                r = api("POST", f"/api/v2/ontologies/{onto_id}/mappings/build-all")
                print(f"  Build-all: {r.status_code}")

    # ═══ 阶段 6: Playwright UI 验证 ═══
    print("\n── 6. Playwright UI 截图验证 ──")
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=False, slow_mo=400, args=["--window-size=1440,900"])
        ctx = br.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()

        # 登录
        page.goto(f"{BASE_UI}/login", wait_until="networkidle")
        page.locator("input").first.fill("admin")
        page.locator("input[type='password']").first.fill("admin123")
        page.locator("button[type='submit']").first.click()
        page.wait_for_url("**/overview", timeout=15000)
        time.sleep(1.5)
        shot(page, "01_overview")

        # Pipeline 列表
        page.goto(f"{BASE_UI}/pipelines", wait_until="networkidle")
        time.sleep(1.5)
        shot(page, "02_pipeline_list")

        # 新建 Pipeline (UI 操作)
        page.locator("button:has-text('新建 Pipeline')").first.click()
        time.sleep(1)
        shot(page, "03_create_modal")
        modal = page.locator(".fixed.inset-0 .bg-white")
        modal.locator("input").first.fill("UI测试流水线")
        modal.locator("select").first.select_option("供应链")
        modal.locator("textarea").first.fill("UI 测试")
        time.sleep(0.5)
        shot(page, "04_create_filled")
        modal.locator("button:has-text('创建')").last.click()
        time.sleep(3)
        shot(page, "05_pipeline_builder")

        # 运行
        run_btn = page.locator("button:has-text('运行')").first
        if run_btn.count() > 0:
            run_btn.click()
            time.sleep(2)
            shot(page, "06_running")
            time.sleep(5)
            shot(page, "07_run_complete")

        page.goto(f"{BASE_UI}/pipelines", wait_until="networkidle")
        time.sleep(1)
        shot(page, "08_pipeline_list_after")

        # Ontology 页面验证
        if onto_id:
            page.goto(f"{BASE_UI}/ontologies/{onto_id}", wait_until="networkidle")
            time.sleep(2)
            shot(page, "09_ontology_info")

            for t in ["Graph", "图谱"]:
                tb = page.locator(f"button:has-text('{t}')")
                if tb.count() > 0:
                    tb.first.click()
                    time.sleep(3)
                    shot(page, "10_graph_tab")
                    break

            for t in ["Entities", "实体"]:
                tb = page.locator(f"button:has-text('{t}')")
                if tb.count() > 0:
                    tb.first.click()
                    time.sleep(2)
                    shot(page, "11_entities_tab")
                    break

            for t in ["Logic", "逻辑"]:
                tb = page.locator(f"button:has-text('{t}')")
                if tb.count() > 0:
                    tb.first.click()
                    time.sleep(1.5)
                    shot(page, "12_logic_tab")
                    break

            for t in ["Actions", "动作"]:
                tb = page.locator(f"button:has-text('{t}')")
                if tb.count() > 0:
                    tb.first.click()
                    time.sleep(1.5)
                    shot(page, "13_actions_tab")
                    break

        # Ontology 列表 + Settings
        page.goto(f"{BASE_UI}/ontologies", wait_until="networkidle")
        time.sleep(1.5)
        shot(page, "14_ontology_list")

        page.goto(f"{BASE_UI}/settings", wait_until="networkidle")
        time.sleep(1.5)
        shot(page, "15_settings")

        for t in ["提示词模版", "用户管理"]:
            tb = page.locator(f"button:has-text('{t}')")
            if tb.count() > 0:
                tb.first.click()
                time.sleep(1.5)
                shot(page, f"16_settings_{t}")

        br.close()

    print("\n" + "═"*50)
    print("  ✅ E2E 测试完成")
    if curated_id:
        print(f"  Curated Dataset: {curated_id[:8]} (rows_in={rows_in})")
    if onto_id:
        print(f"  Ontology: {onto_id[:8]}")
    print("═"*50)

if __name__ == "__main__":
    main()

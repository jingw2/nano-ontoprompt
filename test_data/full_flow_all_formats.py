#!/usr/bin/env python3
"""
full_flow_all_formats.py
供应链 8 种格式文件全流程测试：
  上传文件 → 创建Pipeline并运行 → Curated审批 → 本体提取 → Neo4j图谱
截图保存到 test_data/screenshots/full_flow/
"""
import time, sys, requests, json
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_API = "http://localhost:8000"
BASE_UI  = "http://localhost:5173"
SS_DIR   = Path(__file__).parent / "screenshots" / "full_flow"
SS_DIR.mkdir(parents=True, exist_ok=True)
SUPPLY_DIR = Path(__file__).parent / "供应链"

FILES = [
    "inventory_transactions.csv",
    "logistics_performance.csv",
    "supplier_database.xlsx",
    "supplier_orders.json",
    "supply_chain_strategy.md",
    "warehouse_management.pdf",
    "procurement_policy.docx",
    "supply_chain_review.pptx",
]

# ── API helpers ──────────────────────────────────────────────────────────────
session = requests.Session()

def login_api():
    r = session.post(f"{BASE_API}/api/v1/auth/login",
                     json={"username": "admin", "password": "admin123"})
    token = r.json()["data"]["access_token"]
    session.headers.update({"Authorization": f"Bearer {token}"})
    print("  ✅ API 登录成功")
    return token

def upload_file_api(file_path: Path) -> dict:
    with open(file_path, "rb") as f:
        r = session.post(
            f"{BASE_API}/api/v2/datasets/upload",
            files={"file": (file_path.name, f)},
            headers={"Content-Type": None},
        )
    if r.status_code in (200, 201):
        d = r.json()
        ds_id = d.get("id") or d.get("data", {}).get("id")
        print(f"  ✅ 上传 {file_path.name} → dataset_id={str(ds_id)[:8]}")
        return {"id": ds_id, "name": file_path.name, "ok": True}
    else:
        print(f"  ❌ 上传 {file_path.name} 失败: {r.status_code} {r.text[:100]}")
        return {"name": file_path.name, "ok": False}

def create_and_run_pipeline(dataset_id: str, name: str) -> str | None:
    # 根据文件类型选 route
    if name.endswith((".csv", ".xlsx", ".json")):
        route = "A"
    elif name.endswith((".md", ".docx", ".pptx")):
        route = "C"
    else:
        route = "B"

    r = session.post(f"{BASE_API}/api/v2/pipelines/", json={
        "name": f"全格式测试-{Path(name).stem}-Pipeline",
        "source_dataset_id": dataset_id,
        "route": route,
        "spec": {"steps": ["parse", "clean", "structure"]},
    })
    if r.status_code not in (200, 201):
        print(f"  ❌ 创建 Pipeline 失败 ({name}): {r.text[:100]}")
        return None
    pl_id = r.json().get("id") or r.json().get("data", {}).get("id")

    r2 = session.post(f"{BASE_API}/api/v2/pipelines/{pl_id}/run")
    if r2.status_code in (200, 201, 202):
        print(f"  ✅ Pipeline 运行 ({name}) route={route} pl_id={str(pl_id)[:8]}")
    else:
        print(f"  ⚠️  Pipeline 运行返回 {r2.status_code}")
    return pl_id

def approve_all_pending():
    r = session.get(f"{BASE_API}/api/v2/curated/")
    items = r.json() if isinstance(r.json(), list) else r.json().get("items", [])
    approved = 0
    for item in items:
        if item.get("status") == "pending_review":
            ds_id = item["id"]
            r2 = session.post(f"{BASE_API}/api/v2/curated/{ds_id}/review",
                              params={"action": "approve", "notes": "全格式测试自动审批"})
            if r2.status_code in (200, 201):
                approved += 1
    print(f"  ✅ 自动审批 {approved} 个 Curated 数据集")
    return approved

def sync_neo4j(onto_id: str):
    r = session.post(f"{BASE_API}/api/v2/ontologies/{onto_id}/graph/sync")
    d = r.json()
    print(f"  ✅ Neo4j 同步: entities={d.get('entities')} relations={d.get('relations')}")

# ── UI / Screenshot helpers ───────────────────────────────────────────────────
PASS = FAIL = 0

def shot(page, name, msg=""):
    p = SS_DIR / f"{name}.png"
    page.screenshot(path=str(p))
    if msg: print(f"  📸  {name}.png  {msg}")
    else:   print(f"  📸  {name}.png")

def section(t):
    print(f"\n{'═'*60}\n  {t}\n{'═'*60}")

def click_tab(page, text):
    for sel in [
        f"[role='tab']:has-text('{text}')",
        f"button:has-text('{text}')",
        f"a:has-text('{text}')",
    ]:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                loc.click(timeout=5000)
                return True
        except Exception:
            pass
    return False

# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    # ── Phase 1: API 上传所有文件 ────────────────────────────────────────────
    section("Phase 1 — API 登录 + 上传 8 种格式文件")
    login_api()

    dataset_ids = []
    for fname in FILES:
        fp = SUPPLY_DIR / fname
        if not fp.exists():
            print(f"  ⚠️  文件不存在: {fp}")
            continue
        result = upload_file_api(fp)
        if result["ok"]:
            dataset_ids.append((result["id"], fname))

    print(f"\n  共上传 {len(dataset_ids)} 个数据集")

    # ── Phase 2: 创建并运行 Pipeline ─────────────────────────────────────────
    section("Phase 2 — 为每个数据集创建并运行 Pipeline")
    pipeline_ids = []
    for ds_id, fname in dataset_ids:
        pl_id = create_and_run_pipeline(ds_id, fname)
        if pl_id:
            pipeline_ids.append(pl_id)
    time.sleep(3)  # 等待处理

    # ── Phase 3: 自动审批 Curated ────────────────────────────────────────────
    section("Phase 3 — 自动审批 Curated 数据集")
    approve_all_pending()

    # ── Phase 4: Neo4j 同步 ──────────────────────────────────────────────────
    section("Phase 4 — Neo4j 图谱同步")
    ONTO_ID = "be835a02-63f1-4b21-9b7f-3253510da198"
    sync_neo4j(ONTO_ID)

    # ── Phase 5: Playwright 截图全流程 ───────────────────────────────────────
    section("Phase 5 — Playwright 全流程截图")

    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=False, slow_mo=300,
                                args=["--window-size=1440,900"])
        ctx = br.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()

        # 登录
        page.goto(f"{BASE_UI}/login", wait_until="networkidle")
        time.sleep(1)
        page.locator("input").first.fill("admin")
        page.locator("input[type='password']").first.fill("admin123")
        page.locator("button[type='submit']").first.click()
        page.wait_for_url("**/overview", timeout=15000)
        time.sleep(1)
        shot(page, "01_overview", "概览")

        # Connections
        page.goto(f"{BASE_UI}/pipelines/connections", wait_until="networkidle")
        time.sleep(2)
        shot(page, "02_connections", "数据连接列表")

        # Datasets — 展示所有上传的文件
        page.goto(f"{BASE_UI}/pipelines/datasets", wait_until="networkidle")
        time.sleep(3)
        shot(page, "03_datasets_all", f"数据集列表（含 {len(dataset_ids)} 新上传）")

        # 展开第一个数据集查看 Schema
        try:
            rows = page.locator("table tbody tr")
            if rows.count() > 0:
                rows.first.locator("button, [aria-expanded]").first.click(timeout=3000)
                time.sleep(1)
                shot(page, "04_dataset_expanded", "数据集展开 Schema")
        except Exception:
            pass

        # Transforms — Pipeline 列表
        page.goto(f"{BASE_UI}/pipelines/transforms", wait_until="networkidle")
        time.sleep(2)
        shot(page, "05_transforms", "Transform 流水线列表")

        # 展开第一条 Pipeline 查看详情
        try:
            rows = page.locator("table tbody tr, .pipeline-card, [class*='pipeline']")
            if rows.count() > 0:
                rows.first.click(timeout=3000)
                time.sleep(1.5)
                shot(page, "06_pipeline_detail", "Pipeline 详情")
                page.keyboard.press("Escape")
                page.go_back()
                page.wait_for_load_state("networkidle")
        except Exception:
            pass

        # Curated — 审批后列表
        page.goto(f"{BASE_UI}/pipelines/curated", wait_until="networkidle")
        time.sleep(2)
        shot(page, "07_curated_list", "Curated 数据集列表")

        # 展开第一条 Curated 查看预览
        try:
            rows = page.locator("table tbody tr")
            if rows.count() > 0:
                expand_btn = rows.first.locator("button[aria-label*='展开'], .expand, [class*='chevron'], td:first-child")
                expand_btn.first.click(timeout=3000)
                time.sleep(1.5)
                shot(page, "08_curated_preview", "Curated 数据预览")
                rows.first.click(timeout=2000)
        except Exception:
            pass

        # Ontology 列表
        page.goto(f"{BASE_UI}/ontologies", wait_until="networkidle")
        time.sleep(2)
        shot(page, "09_ontology_list", "本体列表")

        # 进入知识图谱本体详情
        page.goto(f"{BASE_UI}/ontologies/{ONTO_ID}", wait_until="networkidle")
        time.sleep(2)
        shot(page, "10_ontology_info", "本体基本信息")

        # 实体 Tab
        click_tab(page, "实体")
        time.sleep(2)
        shot(page, "11_entities", "实体列表（115 条）")

        # 知识图谱 Tab
        click_tab(page, "知识图谱")
        time.sleep(5)
        shot(page, "12_graph", "知识图谱（Neo4j）")

        # 滚动到查询区
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(1)
        shot(page, "13_graph_query", "图谱查询区")

        # 语义搜索（输入供应链关键词）
        try:
            q_input = page.locator("input[placeholder*='搜索节点'], input[placeholder*='提问']").first
            if q_input.count() > 0:
                q_input.fill("供应商")
                time.sleep(0.5)
                shot(page, "14_graph_search", "图谱搜索高亮")
        except Exception:
            pass

        # 文件 Tab
        page.evaluate("window.scrollTo(0, 0)")
        click_tab(page, "文件上传")
        time.sleep(1)
        shot(page, "15_files_tab", "文件上传 Tab")

        # 设置
        page.goto(f"{BASE_UI}/settings", wait_until="networkidle")
        time.sleep(1)
        shot(page, "16_settings", "系统设置")

        br.close()

    section("完成")
    ss_list = sorted(SS_DIR.glob("*.png"))
    print(f"\n  截图总数: {len(ss_list)}")
    for p in ss_list:
        print(f"    {p.name}")

if __name__ == "__main__":
    main()

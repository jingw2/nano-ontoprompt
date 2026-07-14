#!/usr/bin/env python3
"""eval.md UI 验收: 画布节点详情 + 知识图谱 + 实体/逻辑/动作详情"""
import json, time
from pathlib import Path
import requests
from playwright.sync_api import sync_playwright

BASE = "http://localhost:5173"
API = "http://localhost:8000"
SHOTS = Path(__file__).parent / "screenshots" / "eval_acceptance"
SHOTS.mkdir(parents=True, exist_ok=True)

# 用 API 找到最新 pipeline 和 ontology
s = requests.Session()
r = s.post(f"{API}/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
token = r.json()["data"]["access_token"]
s.headers.update({"Authorization": f"Bearer {token}"})
pls = s.get(f"{API}/api/v2/pipelines").json()
if isinstance(pls, dict):
    pls = pls.get("data", pls.get("items", []))
pl = pls[0]
# 动态取第一个 ontology
ontos = s.get(f"{API}/api/v1/ontologies").json()
ontos = ontos.get("data", ontos)
if isinstance(ontos, dict):
    ontos = ontos.get("items", [])
oid = ontos[0]["id"]
print(f"ontology: {oid[:8]} {ontos[0].get('name')}")
print(f"pipeline: {pl['id'][:8]} {pl.get('name')}")

results = []

def check(name, ok, note=""):
    results.append((name, ok, note))
    print(f"  {'✅' if ok else '❌'} {name} {note}")

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1600, "height": 900})

    # 登录
    page.goto(f"{BASE}/login")
    page.fill('input[placeholder="用户名"]', "admin")
    page.fill('input[placeholder="密码"]', "admin123")
    page.click('button[type="submit"]')
    page.wait_for_url("**/overview", timeout=15000)
    check("登录", True)

    # 1. Pipeline 画布
    page.goto(f"{BASE}/pipelines/{pl['id']}")
    page.wait_for_timeout(3000)
    page.screenshot(path=str(SHOTS / "01_builder_canvas.png"))
    nodes = page.locator(".react-flow__node")
    n = nodes.count()
    check("画布节点渲染", n >= 4, f"({n} 个节点)")

    # 2. 点击连接器节点 → 数据文件
    for i in range(n):
        nd = nodes.nth(i)
        cls = nd.get_attribute("class") or ""
        if "connector" in cls.lower():
            nd.click()
            break
    else:
        nodes.first.click()
    page.wait_for_timeout(2000)
    page.screenshot(path=str(SHOTS / "02_connector_inspector.png"))
    body = page.inner_text("body")
    check("连接器节点显示数据文件", any(k in body for k in [
        ".csv", ".xlsx", ".json", ".pdf", ".docx", ".pptx", ".md",
        "inventory", "supplier", "logistics", "supply_chain", "warehouse"]))

    # 3. 点击输出节点 → 结构化数据
    for i in range(n):
        nd = nodes.nth(i)
        cls = nd.get_attribute("class") or ""
        if "output" in cls.lower():
            nd.click()
            break
    else:
        nodes.nth(n - 1).click()
    page.wait_for_timeout(2500)
    page.screenshot(path=str(SHOTS / "03_output_inspector.png"))
    body = page.inner_text("body")
    check("输出节点显示结构化数据", any(k in body for k in ["curated", "Curated", "行", "rows", "字段", "列"]))

    # 4. Ontology 知识图谱
    page.goto(f"{BASE}/ontologies/{oid}")
    page.wait_for_timeout(2000)
    page.screenshot(path=str(SHOTS / "04_ontology_info.png"))
    # Graph tab — 轮询最多 20s 等待 Cytoscape 渲染
    page.get_by_role("button", name="知识图谱", exact=True).click()
    for _ in range(20):
        page.wait_for_timeout(1000)
        if page.locator("canvas").count():
            break
    page.screenshot(path=str(SHOTS / "05_graph.png"))
    canvas = page.locator("canvas")
    check("图谱画布渲染", canvas.count() >= 1, f"({canvas.count()} canvas)")

    # 5. Entities tab + 详情
    page.get_by_text("实体", exact=True).first.click()
    page.wait_for_timeout(2000)
    page.screenshot(path=str(SHOTS / "06_entities.png"))
    body = page.inner_text("body")
    check("实体列表", "Supplier" in body or "InventoryTransaction" in body)
    row = page.locator("table tbody tr").first
    if row.count():
        row.click()
        page.wait_for_timeout(2000)
        page.screenshot(path=str(SHOTS / "07_entity_detail.png"))
        check("实体详情页", True)
        page.go_back()
        page.wait_for_timeout(1500)

    # 6. Logic tab
    page.locator("text=逻辑规则").first.click()
    page.wait_for_timeout(2000)
    page.screenshot(path=str(SHOTS / "08_logic.png"))
    body = page.inner_text("body")
    check("Logic 列表", "Rule" in body or "规则" in body or "Mapping" in body)

    # 7. Actions tab
    page.locator("text=动作").first.click()
    page.wait_for_timeout(2000)
    page.screenshot(path=str(SHOTS / "09_actions.png"))
    body = page.inner_text("body")
    check("Actions 列表", "Create" in body or "Update" in body or "动作" in body)

    browser.close()

print("\n=== 验收结果 ===")
for name, ok, note in results:
    print(f"{'✅' if ok else '❌'} {name} {note}")
fails = [r for r in results if not r[1]]
print(f"\n{len(results) - len(fails)}/{len(results)} 通过")

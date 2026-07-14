#!/usr/bin/env python3
"""
capture_pipeline_mapping_flow.py
演示 Pipeline Mapping 模式下的完整本体构建流程：
  创建本体(pipeline_mapping) → Curated数据集Tab → 关联Dataset → 配置Mapping → 应用
截图保存到 test_data/screenshots/mapping_flow/
"""
import time, requests, json
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_API = "http://localhost:8000"
BASE_UI  = "http://localhost:5173"
SS_DIR   = Path(__file__).parent / "screenshots" / "mapping_flow"
SS_DIR.mkdir(parents=True, exist_ok=True)

session = requests.Session()

def login_api():
    r = session.post(f"{BASE_API}/api/v1/auth/login",
                     json={"username": "admin", "password": "admin123"})
    token = r.json()["data"]["access_token"]
    session.headers.update({"Authorization": f"Bearer {token}"})
    print("  ✅ API 登录成功")
    return token

def get_approved_curated():
    r = session.get(f"{BASE_API}/api/v2/curated")
    items = r.json() if isinstance(r.json(), list) else []
    approved = [d for d in items if d.get("status") == "approved"]
    print(f"  ✅ 已审批 Curated Dataset: {len(approved)} 个")
    return approved

def create_pipeline_mapping_ontology(name: str) -> str:
    r = session.post(f"{BASE_API}/api/v1/ontologies", json={
        "name": name,
        "domain": "供应链",
        "description": "通过 Pipeline Mapping 构建的供应链本体",
        "build_mode": "pipeline_mapping",
    })
    oid = r.json().get("data", {}).get("id") or r.json().get("id")
    print(f"  ✅ 创建本体 build_mode=pipeline_mapping id={str(oid)[:8]}")
    return oid

def shot(page, name, msg=""):
    p = SS_DIR / f"{name}.png"
    page.screenshot(path=str(p))
    print(f"  📸  {name}.png  {msg}")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("\n" + "═"*60)
    print("  Pipeline Mapping 流程截图")
    print("═"*60)

    login_api()
    approved_datasets = get_approved_curated()
    if not approved_datasets:
        print("  ⚠️  没有已审批的 Curated Dataset，请先运行 full_flow_all_formats.py")
        return

    # 创建 pipeline_mapping 模式本体
    onto_id = create_pipeline_mapping_ontology("供应链本体-PipelineMapping演示")
    if not onto_id:
        print("  ❌ 本体创建失败")
        return

    time.sleep(1)

    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=False, slow_mo=400,
                                args=["--window-size=1440,900"])
        ctx = br.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()

        # 登录
        page.goto(f"{BASE_UI}/login", wait_until="networkidle")
        page.locator("input").first.fill("admin")
        page.locator("input[type='password']").first.fill("admin123")
        page.locator("button[type='submit']").first.click()
        page.wait_for_url("**/overview", timeout=15000)
        time.sleep(1)

        # 进入新建本体的详情页
        page.goto(f"{BASE_UI}/ontologies/{onto_id}", wait_until="networkidle")
        time.sleep(2)
        shot(page, "01_ontology_info", "本体基本信息（pipeline_mapping 模式）")

        # 点击 "Curated 数据集" Tab
        curated_tab = page.locator("button:has-text('Curated 数据集'), [role='tab']:has-text('Curated 数据集')")
        if curated_tab.count() > 0:
            curated_tab.first.click(timeout=5000)
            time.sleep(1.5)
            shot(page, "02_curated_tab_empty", "Curated 数据集 Tab（初始空状态）")
            print("  ✅ 点击 Curated 数据集 Tab 成功")
        else:
            print("  ❌ 未找到 Curated 数据集 Tab")
            shot(page, "02_curated_tab_missing", "Curated Tab 未找到")
            br.close()
            return

        # 点击"关联数据集"
        link_btn = page.locator("button:has-text('关联数据集')")
        if link_btn.count() > 0:
            link_btn.first.click(timeout=5000)
            time.sleep(1)
            shot(page, "03_link_panel", "关联数据集面板")
            print("  ✅ 打开关联面板")
        else:
            print("  ⚠️  未找到关联数据集按钮")

        # 选择第一个 approved dataset
        select_el = page.locator("select").first
        if select_el.count() > 0:
            ds_name = approved_datasets[0].get("name", "")
            # Select by index (option 1, skipping placeholder)
            select_el.select_option(index=1)
            time.sleep(0.5)
            shot(page, "04_dataset_selected", f"选择数据集")
            print(f"  ✅ 选择数据集")

            # 点击"自动生成 Mapping"
            suggest_btn = page.locator("button:has-text('自动生成 Mapping')")
            if suggest_btn.count() > 0:
                suggest_btn.first.click(timeout=5000)
                # 等待 LLM 建议生成
                time.sleep(8)
                shot(page, "05_mapping_suggestion", "自动生成 Mapping 建议")
                print("  ✅ 自动生成 Mapping 建议")

                # 点击确认保存
                save_btn = page.locator("button:has-text('确认并保存 Mapping')")
                if save_btn.count() > 0:
                    save_btn.first.click(timeout=5000)
                    time.sleep(2)
                    shot(page, "06_mapping_saved", "Mapping 已保存")
                    print("  ✅ Mapping 保存成功")
                else:
                    shot(page, "06_save_btn_missing", "保存按钮未找到")
            else:
                shot(page, "05_suggest_btn_missing", "自动生成按钮未找到")
        else:
            print("  ⚠️  未找到数据集下拉框")

        # 查看已保存的 Mapping 列表
        time.sleep(1)
        shot(page, "07_mapping_list", "Mapping 列表（已保存）")

        # 点击"应用 Mapping"
        apply_btn = page.locator("button:has-text('应用 Mapping')")
        if apply_btn.count() > 0:
            apply_btn.first.click(timeout=5000)
            # 等待应用完成
            time.sleep(5)
            shot(page, "08_mapping_applied", "Mapping 应用完成（实体写入）")
            print("  ✅ Mapping 应用成功")
        else:
            print("  ⚠️  未找到应用 Mapping 按钮")

        # 切换到实体 tab 查看写入结果
        entities_tab = page.locator("button:has-text('实体'), [role='tab']:has-text('实体')")
        if entities_tab.count() > 0:
            entities_tab.first.click(timeout=5000)
            time.sleep(2)
            shot(page, "09_entities_after_mapping", "实体列表（Mapping 写入后）")
            print("  ✅ 实体 Tab 截图完成")

        br.close()

    print("\n  截图总数:", len(sorted(SS_DIR.glob("*.png"))))
    for p in sorted(SS_DIR.glob("*.png")):
        print(f"    {p.name}")

if __name__ == "__main__":
    main()

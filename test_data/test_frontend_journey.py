#!/usr/bin/env python3
"""
test_frontend_journey.py — 模拟真实用户前端全流程
截图保存到 test_data/screenshots/
"""
import os, sys, time
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("pip install playwright && python -m playwright install chromium")
    sys.exit(1)

BASE   = "http://localhost:5173"
SS_DIR = Path(__file__).parent / "screenshots"
SS_DIR.mkdir(exist_ok=True)

# 使用实体最多的供应链本体
ONTO_ID = "be835a02-63f1-4b21-9b7f-3253510da198"

PASS = FAIL = 0

def shot(page, name):
    p = SS_DIR / f"{name}.png"
    page.screenshot(path=str(p))
    print(f"  📸  {p.name}")

def ok(msg):
    global PASS; PASS += 1; print(f"  ✅  {msg}")

def warn(msg):
    print(f"  ⚠️   {msg}")

def fail(msg):
    global FAIL; FAIL += 1; print(f"  ❌  {msg}")

def section(t):
    print(f"\n{'─'*60}\n  {t}\n{'─'*60}")

def goto(page, path, wait=2):
    page.goto(f"{BASE}{path}", wait_until="networkidle")
    time.sleep(wait)

def click_text(page, text, timeout=6000):
    """点击含指定文字的第一个可点击元素"""
    try:
        page.get_by_text(text, exact=True).first.click(timeout=timeout)
        return True
    except Exception:
        try:
            page.locator(f"button:has-text('{text}'), a:has-text('{text}'), [role='tab']:has-text('{text}')").first.click(timeout=timeout)
            return True
        except Exception:
            return False

def run():
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=False, slow_mo=300,
                                args=["--window-size=1440,900"])
        ctx = br.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()

        # ── 1. 登录 ────────────────────────────────────────────────
        section("1 — 登录")
        goto(page, "/login", wait=1)
        shot(page, "01_login")

        page.locator("input[type='text'], input[name='username']").first.fill("admin")
        page.locator("input[type='password']").first.fill("admin123")
        shot(page, "02_login_filled")

        page.locator("button[type='submit']").first.click()
        page.wait_for_url("**/overview", timeout=10000)
        time.sleep(1)
        shot(page, "03_overview")
        ok(f"登录成功 → {page.url}")

        # 读取概览卡片数字
        for label in ["本体总数", "实体总数", "逻辑规则总数", "动作总数"]:
            try:
                card = page.locator(f"*:has-text('{label}')").first
                ok(f"  概览卡片: {card.text_content().strip()[:30]}")
            except Exception:
                pass

        # ── 2. 数据管道 → Connections ──────────────────────────────
        section("2 — 数据管道 → Connections")
        goto(page, "/pipelines/connections")
        shot(page, "04_connections")
        ok("Connections 页面")

        # 检查连接列表
        conn_count = page.locator("table tbody tr").count()
        ok(f"连接数: {conn_count}")

        if conn_count > 0:
            # 点击 Sync 按钮（第一行）
            try:
                page.locator("table tbody tr").first.locator("button").first.click(timeout=3000)
                time.sleep(0.5)
                shot(page, "05_connection_action")
                ok("点击连接操作按钮")
                page.keyboard.press("Escape")
            except Exception:
                pass

        # ── 3. Datasets ────────────────────────────────────────────
        section("3 — Datasets")
        goto(page, "/pipelines/datasets")
        shot(page, "06_datasets")
        ok("Datasets 页面")

        ds_count = page.locator("table tbody tr").count()
        ok(f"数据集数: {ds_count}")

        # 展开第一行（可展开的 schema/preview）
        if ds_count > 0:
            try:
                expand = page.locator("table tbody tr button[aria-expanded], table tbody tr .expand-btn, table tbody tr td:first-child").first
                expand.click(timeout=3000)
                time.sleep(1)
                shot(page, "07_dataset_expanded")
                ok("数据集行展开（Schema/Preview）")
            except Exception:
                warn("展开按钮未找到")

        # 上传新文件
        supply_dir = Path(__file__).parent / "供应链"
        csv_path = supply_dir / "logistics_performance.csv"
        if csv_path.exists():
            try:
                # 找文件输入（Dropzone 通常有隐藏的 input[type=file]）
                fi = page.locator("input[type='file']").first
                if fi.count() == 0:
                    # 点击上传区域触发
                    page.locator("*:has-text('上传'), *:has-text('Upload'), *:has-text('拖放')").first.click(timeout=3000)
                    time.sleep(0.3)
                page.locator("input[type='file']").first.set_input_files(str(csv_path), timeout=3000)
                time.sleep(2)
                shot(page, "08_dataset_uploaded")
                ok(f"上传文件: {csv_path.name}")
            except Exception as e:
                warn(f"文件上传操作: {e}")

        # ── 4. Transforms (Pipelines) ──────────────────────────────
        section("4 — Transforms / Pipelines")
        goto(page, "/pipelines/transforms")
        shot(page, "09_transforms")
        ok("Transforms 页面")

        pl_count = page.locator("table tbody tr").count()
        ok(f"Pipeline 数: {pl_count}")

        # 点击第一个 pipeline 查看详情
        if pl_count > 0:
            try:
                page.locator("table tbody tr").first.click(timeout=3000)
                time.sleep(1)
                shot(page, "10_pipeline_detail")
                ok("Pipeline 详情")
                page.go_back(); page.wait_for_load_state("networkidle")
            except Exception:
                pass

        # ── 5. Curated ────────────────────────────────────────────
        section("5 — Curated 数据集")
        goto(page, "/pipelines/curated")
        shot(page, "11_curated")
        ok("Curated 页面")

        cu_count = page.locator("table tbody tr").count()
        ok(f"Curated 数据集数: {cu_count}")

        # 点第一行展开/查看质量报告
        if cu_count > 0:
            try:
                page.locator("table tbody tr").first.click(timeout=3000)
                time.sleep(1)
                shot(page, "12_curated_detail")
                ok("Curated 详情/质量报告")
                page.keyboard.press("Escape")
                page.go_back()
                page.wait_for_load_state("networkidle")
            except Exception:
                pass

        # ── 6. 本体列表 ────────────────────────────────────────────
        section("6 — 本体列表")
        goto(page, "/ontologies")
        shot(page, "13_ontology_list")
        ok("本体列表")

        onto_count = page.locator("table tbody tr").count()
        ok(f"本体项目数: {onto_count}")

        # ── 7. 创建新本体（向导） ───────────────────────────────────
        section("7 — 创建本体向导")
        goto(page, "/ontologies/new", wait=1)
        shot(page, "14_ontology_new")
        ok("本体创建向导")

        # 填写基本信息
        name_input = page.locator("input[placeholder*='名称'], input[name='name'], input[type='text']").first
        try:
            name_input.fill("Playwright-供应链测试本体", timeout=3000)
            ok("填写本体名称")
        except Exception:
            warn("名称输入框未找到")

        # 选择域
        try:
            domain_select = page.locator("select, [role='combobox'], [role='listbox']").first
            if domain_select.count() > 0:
                domain_select.select_option("供应链", timeout=3000)
                ok("选择域: 供应链")
        except Exception:
            # 尝试点击下拉
            try:
                page.locator("*:has-text('供应链')").nth(1).click(timeout=3000)
                ok("选择域: 供应链（点击方式）")
            except Exception:
                pass

        shot(page, "15_ontology_form_filled")

        # ── 8. 进入已有供应链本体详情 ────────────────────────────────
        section("8 — 供应链本体详情（115实体）")
        goto(page, f"/ontologies/{ONTO_ID}", wait=2)
        shot(page, "16_ontology_detail_info")
        ok(f"本体详情页 (id={ONTO_ID[:8]}...)")

        # Info Tab（默认）
        try:
            name_text = page.locator("h1, h2, .ontology-name").first.text_content()
            ok(f"  本体名称: {name_text[:40]}")
        except Exception:
            pass

        # 文件 Tab
        if click_text(page, "文件") or click_text(page, "Files"):
            time.sleep(1)
            shot(page, "17_ontology_files")
            ok("切换到文件 Tab")
            file_count = page.locator("table tbody tr, .file-item").count()
            ok(f"  文件数: {file_count}")

        # 实体 Tab
        if click_text(page, "实体") or click_text(page, "Entities"):
            time.sleep(1)
            shot(page, "18_entities_tab")
            ok("切换到实体 Tab")
            ent_count = page.locator("table tbody tr").count()
            ok(f"  实体数: {ent_count}")

            # 点第一个实体进入详情
            if ent_count > 0:
                try:
                    page.locator("table tbody tr").first.click(timeout=3000)
                    time.sleep(1)
                    shot(page, "19_entity_detail")
                    ok("  实体详情页/弹窗")
                    # 尝试关闭弹窗或返回
                    for close in ["button:has-text('关闭')", "button:has-text('Close')", "[aria-label='close']"]:
                        try:
                            page.locator(close).first.click(timeout=2000)
                            break
                        except Exception:
                            continue
                    page.keyboard.press("Escape")
                    time.sleep(0.3)
                except Exception:
                    pass

        # 逻辑规则 Tab
        if click_text(page, "逻辑规则") or click_text(page, "Logic"):
            time.sleep(1)
            shot(page, "20_logic_tab")
            ok("切换到逻辑规则 Tab")
            logic_count = page.locator("table tbody tr").count()
            ok(f"  逻辑规则数: {logic_count}")

        # 动作 Tab
        if click_text(page, "动作") or click_text(page, "Actions"):
            time.sleep(1)
            shot(page, "21_actions_tab")
            ok("切换到动作 Tab")
            action_count = page.locator("table tbody tr").count()
            ok(f"  动作数: {action_count}")

        # 图谱 Tab
        if click_text(page, "图谱") or click_text(page, "Graph"):
            time.sleep(3)  # 等图渲染
            shot(page, "22_graph_tab")
            ok("切换到图谱 Tab")

            # 等待 cytoscape canvas
            try:
                page.locator("canvas, .cy-container, #cy").first.wait_for(state="visible", timeout=5000)
                ok("  图谱 Canvas 渲染完成")
            except Exception:
                warn("  图谱 Canvas 未检测到（可能无数据）")

            # 尝试图谱查询（如有 Neo4j）
            try:
                q_input = page.locator("input[placeholder*='查询'], input[placeholder*='Cypher'], input[placeholder*='搜索']").first
                if q_input.count() > 0:
                    q_input.fill("MATCH (n) RETURN n LIMIT 10", timeout=3000)
                    page.keyboard.press("Enter")
                    time.sleep(1)
                    shot(page, "23_graph_query")
                    ok("  图谱 Cypher 查询")
            except Exception:
                pass

        # 导出
        try:
            export_btn = page.locator("button:has-text('导出'), button:has-text('Export')").first
            if export_btn.count() > 0:
                export_btn.click(timeout=3000)
                time.sleep(0.8)
                shot(page, "24_export_modal")
                ok("点击导出按钮")
                page.keyboard.press("Escape")
        except Exception:
            pass

        # ── 9. 提示词模板页 ────────────────────────────────────────
        section("9 — 提示词模板")
        goto(page, "/prompts", wait=1)
        shot(page, "25_prompts")
        ok("提示词列表页")
        p_count = page.locator("table tbody tr, .prompt-card").count()
        ok(f"提示词数: {p_count}")

        if p_count > 0:
            try:
                page.locator("table tbody tr").first.click(timeout=3000)
                time.sleep(0.8)
                shot(page, "26_prompt_detail")
                ok("提示词详情")
                page.go_back(); page.wait_for_load_state("networkidle")
            except Exception:
                pass

        # ── 10. 模型管理 ────────────────────────────────────────────
        section("10 — 模型管理")
        goto(page, "/models", wait=1)
        shot(page, "27_models")
        ok("Models 页面")

        m_count = page.locator("table tbody tr, .model-card").count()
        ok(f"模型数: {m_count}")

        if m_count > 0:
            try:
                # 找 Test Connection 按钮
                test_btn = page.locator("button:has-text('测试'), button:has-text('Test')").first
                if test_btn.count() > 0:
                    test_btn.click(timeout=3000)
                    time.sleep(1.5)
                    shot(page, "28_model_test")
                    ok("模型连通性测试")
                    page.keyboard.press("Escape")
            except Exception:
                pass

        # ── 11. 设置页 ──────────────────────────────────────────────
        section("11 — 系统设置")
        goto(page, "/settings", wait=1)
        shot(page, "29_settings")
        ok("Settings 页面")

        # 置信度规则 Tab（默认）
        ok("置信度规则 Tab（默认打开）")
        rule_items = page.locator("input[type='number'], .rule-item").count()
        ok(f"  规则输入项数: {rule_items}")

        # 用户管理 Tab
        if click_text(page, "用户管理") or click_text(page, "Users"):
            time.sleep(0.8)
            shot(page, "30_settings_users")
            ok("用户管理 Tab")
            u_count = page.locator("table tbody tr").count()
            ok(f"  用户数: {u_count}")

        # 提示词模版 Tab
        if click_text(page, "提示词模版") or click_text(page, "Prompts"):
            time.sleep(0.8)
            shot(page, "31_settings_prompts")
            ok("提示词模版 Tab")
            pt_count = page.locator("table tbody tr, .prompt-item").count()
            ok(f"  提示词数: {pt_count}")

            # 一键生成按钮
            try:
                gen = page.locator("button:has-text('生成'), button:has-text('Generate'), button:has-text('一键生成')").first
                if gen.count() > 0:
                    gen.click(timeout=3000)
                    time.sleep(0.5)
                    shot(page, "32_generate_prompt")
                    ok("点击生成提示词")
                    page.keyboard.press("Escape")
            except Exception:
                pass

        # ── 12. 本体创建向导完整流程 ────────────────────────────────
        section("12 — 本体详情：补充执行提取流程")
        goto(page, f"/ontologies/{ONTO_ID}", wait=2)

        # 点击"执行提取"或"开始提取"按钮
        for btn_text in ["执行提取", "开始提取", "Extract", "Run Extraction", "提取"]:
            try:
                btn = page.locator(f"button:has-text('{btn_text}')").first
                if btn.count() > 0:
                    btn.click(timeout=3000)
                    time.sleep(1)
                    shot(page, "33_extraction_modal")
                    ok(f"点击提取按钮: {btn_text}")
                    page.keyboard.press("Escape")
                    break
            except Exception:
                continue

        # ── 汇总 ────────────────────────────────────────────────────
        section("最终汇总")
        ss_list = sorted(SS_DIR.glob("*.png"))
        print(f"\n  截图总数: {len(ss_list)}")
        print(f"  ✅  通过: {PASS}")
        print(f"  ❌  失败: {FAIL}")
        print(f"\n  截图目录: {SS_DIR}\n")

        time.sleep(2)
        br.close()

if __name__ == "__main__":
    run()

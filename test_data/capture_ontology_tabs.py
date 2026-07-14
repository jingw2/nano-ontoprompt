#!/usr/bin/env python3
"""专项截图：本体详情的 知识图谱/逻辑规则/动作 三个 tab"""
import time, sys
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("pip install playwright && python -m playwright install chromium")
    sys.exit(1)

BASE   = "http://localhost:5173"
SS_DIR = Path(__file__).parent / "screenshots"
SS_DIR.mkdir(exist_ok=True)

ONTO_ID = "be835a02-63f1-4b21-9b7f-3253510da198"

def shot(page, name):
    p = SS_DIR / f"{name}.png"
    page.screenshot(path=str(p), full_page=False)
    print(f"  📸  {p.name}")

def section(t):
    print(f"\n{'─'*60}\n  {t}\n{'─'*60}")

with sync_playwright() as pw:
    br = pw.chromium.launch(headless=False, slow_mo=400,
                            args=["--window-size=1440,900"])
    ctx = br.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()

    # 登录
    section("登录")
    page.goto(f"{BASE}/login", wait_until="networkidle")
    page.locator("input[type='text'], input[name='username']").first.fill("admin")
    page.locator("input[type='password']").first.fill("admin123")
    page.locator("button[type='submit']").first.click()
    page.wait_for_url("**/overview", timeout=10000)
    time.sleep(1)
    print("  ✅ 登录成功")

    # 进入本体详情
    section(f"本体详情 {ONTO_ID[:8]}")
    page.goto(f"{BASE}/ontologies/{ONTO_ID}", wait_until="networkidle")
    time.sleep(2)
    shot(page, "onto_info_tab")

    # 打印所有 tab 文字
    tabs = page.locator("[role='tab'], .tab, nav a, .tabs button")
    print(f"  共找到 tab 元素: {tabs.count()}")
    for i in range(tabs.count()):
        try:
            txt = tabs.nth(i).text_content()
            print(f"    Tab[{i}]: '{txt.strip()}'")
        except:
            pass

    # 尝试点击各 tab（用 index 和 text 两种方式）
    tab_map = [
        ("知识图谱", "graph_tab"),
        ("实体", "entities_tab"),
        ("逻辑规则", "logic_tab"),
        ("动作", "actions_tab"),
        ("文件上传", "files_tab"),
    ]

    for tab_text, shot_name in tab_map:
        section(f"Tab: {tab_text}")
        clicked = False

        # 方式1：role=tab
        try:
            loc = page.get_by_role("tab", name=tab_text)
            if loc.count() > 0:
                loc.first.click(timeout=5000)
                clicked = True
                print(f"  ✅ role=tab 点击成功: {tab_text}")
        except Exception as e:
            pass

        # 方式2：text 匹配（包含）
        if not clicked:
            try:
                loc = page.locator(f"button:has-text('{tab_text}'), [role='tab']:has-text('{tab_text}'), a:has-text('{tab_text}')")
                if loc.count() > 0:
                    loc.first.click(timeout=5000)
                    clicked = True
                    print(f"  ✅ text 点击成功: {tab_text}")
            except Exception as e:
                pass

        # 方式3：精确文字
        if not clicked:
            try:
                page.get_by_text(tab_text, exact=True).first.click(timeout=5000)
                clicked = True
                print(f"  ✅ get_by_text 点击成功: {tab_text}")
            except Exception:
                pass

        if clicked:
            time.sleep(2)
            shot(page, shot_name)

            # 图谱额外等待 canvas
            if "graph" in shot_name:
                try:
                    page.locator("canvas, .cy-container, #cy, svg.graph").first.wait_for(
                        state="visible", timeout=8000)
                    time.sleep(2)
                    shot(page, "graph_tab_loaded")
                    print("  ✅ 图谱 canvas 渲染完成")
                except Exception:
                    print("  ⚠️ 图谱 canvas 未检测到")

            # 逻辑规则：打印条数
            if "logic" in shot_name:
                cnt = page.locator("table tbody tr, .rule-item, li.logic-rule").count()
                print(f"  逻辑规则数: {cnt}")

            # 动作：打印条数
            if "actions" in shot_name:
                cnt = page.locator("table tbody tr, .action-item, li.action").count()
                print(f"  动作数: {cnt}")
        else:
            print(f"  ❌ 无法点击 tab: {tab_text}")

    section("完成")
    print(f"\n截图已保存到 {SS_DIR}")
    br.close()

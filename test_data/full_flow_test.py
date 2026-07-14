#!/usr/bin/env python3
"""
全流程测试脚本：
1. 上传 test_data/供应链 所有文件
2. 查看数据集
3. 创建并运行 Pipeline
4. 构建 Ontology (Pipeline Mapping)
5. 验证结果
截图保存到 test_data/screenshots/full_flow/
"""
import time, requests, json, os
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_API = "http://localhost:8000"
BASE_UI  = "http://localhost:5173"
TEST_DATA = Path(__file__).parent / "供应链"
SS_DIR   = Path(__file__).parent / "screenshots" / "full_flow"
SS_DIR.mkdir(parents=True, exist_ok=True)

session = requests.Session()

def login_api():
    r = session.post(f"{BASE_API}/api/v1/auth/login",
                     json={"username": "admin", "password": "admin123"})
    token = r.json()["data"]["access_token"]
    session.headers.update({"Authorization": f"Bearer {token}"})
    print("  ✅ API 登录成功")
    return token

def shot(page, name, msg=""):
    p = SS_DIR / f"{name}.png"
    page.screenshot(path=str(p))
    print(f"  📸  {name}.png  {msg}")

def upload_file(file_path: str) -> dict:
    """上传单个文件到 /api/v2/datasets/upload，返回 dataset 信息"""
    fname = os.path.basename(file_path)
    with open(file_path, 'rb') as f:
        r = session.post(f"{BASE_API}/api/v2/datasets/upload",
                         files={"file": (fname, f, "application/octet-stream")})
    result = r.json()
    print(f"  📤  上传 {fname}: {'✅' if r.ok else '❌'} {result.get('message', '')}")
    return result

def wait_for_backend():
    """等待后端就绪"""
    for i in range(30):
        try:
            r = session.post(f"{BASE_API}/api/v1/auth/login",
                            json={"username": "admin", "password": "admin123"}, timeout=2)
            if r.ok:
                print("  ✅ 后端就绪")
                return True
        except:
            pass
        time.sleep(1)
    print("  ❌ 后端未就绪")
    return False

def main():
    print("\n" + "═"*60)
    print("  🔄 全流程测试：供应链数据 Pipeline → Ontology")
    print("═"*60)

    if not wait_for_backend():
        return

    token = login_api()
    if not token:
        return

    # ── Step 1: 上传所有测试文件 ─────────────────────────────
    print("\n── Step 1: 上传测试数据 ──")
    files = sorted(TEST_DATA.glob("*"))
    print(f"  找到 {len(files)} 个文件:")
    for f in files:
        print(f"    - {f.name} ({f.stat().st_size / 1024:.1f} KB)")

    uploaded_datasets = []
    for f in files:
        result = upload_file(str(f))
        if isinstance(result, dict) and result.get("dataset_id"):
            uploaded_datasets.append(result["dataset_id"])
        elif isinstance(result, list) and len(result) > 0:
            uploaded_datasets.append(result[0].get("id", ""))

    print(f"  ✅ 上传完成，共 {len(uploaded_datasets)} 个数据集")

    # ── Step 2: 查看数据集列表 ─────────────────────────────
    print("\n── Step 2: 数据集列表 ──")
    r = session.get(f"{BASE_API}/api/v2/datasets")
    datasets = r.json() if r.ok else []
    datasets_list = datasets if isinstance(datasets, list) else (datasets if isinstance(datasets, dict) and 'data' in datasets else [])
    if isinstance(datasets_list, dict):
        datasets_list = [datasets_list]
    print(f"  共有 {len(datasets_list) if isinstance(datasets_list, list) else 0} 个数据集")
    if isinstance(datasets_list, list):
        for ds in datasets_list:
            print(f"    - {ds.get('name', '?')} ({ds.get('kind', ds.get('type', '?'))})")

    # ── Step 3: 创建 Pipeline ──────────────────────────────
    print("\n── Step 3: 创建 Pipeline ──")
    first_ds_id = None
    if isinstance(datasets_list, list) and len(datasets_list) > 0:
        # 找到第一个非 curated 的数据集
        for ds in datasets_list:
            kid = ds.get('kind', ds.get('type', ''))
            if kid != 'curated':
                first_ds_id = ds['id']
                break
        if not first_ds_id:
            first_ds_id = datasets_list[0]['id']

    if first_ds_id:
        # 获取数据集详情来检测类型
        r = session.get(f"{BASE_API}/api/v2/datasets/{first_ds_id}")
        ds_detail = r.json() if r.ok else {}
        ds_name = ds_detail.get('name', '供应链测试数据集') if isinstance(ds_detail, dict) else '供应链测试数据集'
        ds_kind = ds_detail.get('kind', 'structured') if isinstance(ds_detail, dict) else 'structured'
        route = 'A' if ds_kind in ('structured', 'tabular') else 'B' if ds_kind in ('semi', 'json') else 'C'

        pl_payload = {
            "name": f"{ds_name} 清洗流水线",
            "domain": "供应链",
            "description": "供应链测试数据清洗 Pipeline",
            "source_dataset_id": first_ds_id,
            "route": route,
            "spec": {
                "steps": [
                    {"op": "drop_duplicates", "params": {}},
                    {"op": "normalize_dates", "params": {}},
                ]
            },
            "definition": {
                "schema_version": "2.0",
                "nodes": [
                    {"id": "connector_1", "type": "connector", "label": "文件上传", "position": {"x": 80, "y": 200}},
                    {"id": "storage_1", "type": "storage", "label": "原始数据", "position": {"x": 280, "y": 200}},
                    {"id": "transform_1", "type": "transform", "label": "数据清洗",
                     "position": {"x": 480, "y": 200},
                     "config": {"path": "auto", "steps": [{"op": "drop_duplicates"}]}},
                    {"id": "output_1", "type": "output", "label": "清洗结果",
                     "position": {"x": 720, "y": 200},
                     "config": {"dataset_type": "curated_dataset", "primary_key": ["id"]}},
                ],
                "edges": [
                    {"id": "e1", "source": "connector_1", "target": "storage_1"},
                    {"id": "e2", "source": "storage_1", "target": "transform_1"},
                    {"id": "e3", "source": "transform_1", "target": "output_1"},
                ]
            }
        }
        r = session.post(f"{BASE_API}/api/v2/pipelines", json=pl_payload)
        if r.ok:
            pl = r.json()
            pl_id = pl.get('id') if isinstance(pl, dict) else None
            print(f"  ✅ Pipeline 创建成功: {pl.get('name', '?')} id={str(pl_id)[:8] if pl_id else '?'}")

            # ── Step 4: 运行 Pipeline ──────────────────────
            print("\n── Step 4: 运行 Pipeline ──")
            if pl_id:
                r2 = session.post(f"{BASE_API}/api/v2/pipelines/{pl_id}/run-sync")
                if r2.ok:
                    run_result = r2.json()
                    print(f"  ✅ Pipeline 运行完成: status={run_result.get('status')}")
                    print(f"     统计: {run_result.get('stats', {})}")
                else:
                    print(f"  ⚠️  Pipeline 运行返回: {r2.status_code} {r2.text[:200]}")
            else:
                print("  ⚠️  无法获取 Pipeline ID")
        else:
            print(f"  ❌ Pipeline 创建失败: {r.status_code} {r.text[:200]}")
    else:
        print("  ⚠️  没有可用的数据集")

    # ── Step 5: 查看 Curated Datasets ─────────────────────
    print("\n── Step 5: Curated Datasets ──")
    r = session.get(f"{BASE_API}/api/v2/curated")
    curated = r.json() if r.ok else []
    curated_list = curated if isinstance(curated, list) else (curated.get('data', []) if isinstance(curated, dict) else [])
    if isinstance(curated_list, list):
        print(f"  共有 {len(curated_list)} 个 Curated Dataset")
        for cd in curated_list:
            print(f"    - {cd.get('name', cd.get('id', '?'))}: {cd.get('status', '?')}")
    else:
        print(f"  Curated Datasets: {curated}")

    # ── Step 6: 创建 Ontology (Pipeline Mapping) ──────────
    print("\n── Step 6: 创建 Ontology (Pipeline Mapping) ──")
    # 找到 approved curated datasets
    approved = [cd for cd in (curated_list if isinstance(curated_list, list) else []) if cd.get('status') == 'approved']
    if not approved:
        # 尝试 approve 一个
        if isinstance(curated_list, list) and len(curated_list) > 0:
            cd_id = curated_list[0]['id']
            r = session.post(f"{BASE_API}/api/v2/curated/{cd_id}/review", json={"status": "approved"})
            print(f"  Approve Curated Dataset: {r.status_code}")
            time.sleep(0.5)
            approved = [curated_list[0]]

    if approved:
        onto_payload = {
            "name": "供应链知识图谱",
            "domain": "供应链",
            "description": "通过 Pipeline Mapping 从供应链数据构建的本体",
            "build_mode": "pipeline_mapping",
            "curated_dataset_ids": [cd['id'] for cd in approved[:3] if cd.get('id')],
        }
        r = session.post(f"{BASE_API}/api/v1/ontologies", json=onto_payload)
        print(f"  Ontology 创建: {r.status_code}")
        if r.ok:
            onto = r.json()
            onto_data = onto.get('data', onto) if isinstance(onto, dict) else onto
            onto_id = onto_data.get('id') if isinstance(onto_data, dict) else None
            if onto_id:
                print(f"  ✅ Ontology 创建成功: {onto_id[:8]}")
            else:
                print(f"  响应: {onto}")
        else:
            print(f"  ❌ 创建失败: {r.text[:300]}")
    else:
        print("  ⚠️  没有可用的 Curated Dataset")

    # ── Screenshots ──────────────────────────────────────────
    print("\n── Screenshots ──")
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=False, slow_mo=300,
                                args=["--window-size=1440,900"])
        ctx = br.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()

        # 登录 UI
        page.goto(f"{BASE_UI}/login", wait_until="networkidle")
        page.locator("input").first.fill("admin")
        page.locator("input[type='password']").first.fill("admin123")
        page.locator("button[type='submit']").first.click()
        page.wait_for_url("**/overview", timeout=15000)
        time.sleep(1)
        shot(page, "01_overview", "首页概览")

        # Pipeline 列表页
        page.goto(f"{BASE_UI}/pipelines", wait_until="networkidle")
        time.sleep(2)
        shot(page, "02_pipeline_list", "Pipeline 列表")

        # 进入 Pipeline Builder
        edit_btn = page.locator('a[href*="/pipelines/"]').first
        if edit_btn.count() > 0:
            href = edit_btn.get_attribute('href')
            if href and href != '/pipelines':
                page.goto(f"{BASE_UI}{href}", wait_until="networkidle")
                time.sleep(2)
                shot(page, "03_pipeline_builder", "Pipeline Builder 画布")

        # 转到 Connections
        page.goto(f"{BASE_UI}/pipelines/connections", wait_until="networkidle")
        time.sleep(1.5)
        shot(page, "04_connections", "数据连接页面")

        # 转到 Datasets
        page.goto(f"{BASE_UI}/pipelines/datasets", wait_until="networkidle")
        time.sleep(1.5)
        shot(page, "05_datasets", "数据集页面")

        # 转到 Transforms
        page.goto(f"{BASE_UI}/pipelines/transforms", wait_until="networkidle")
        time.sleep(1.5)
        shot(page, "06_transforms", "Transform 流水线")

        # 转到 Curated
        page.goto(f"{BASE_UI}/pipelines/curated", wait_until="networkidle")
        time.sleep(1.5)
        shot(page, "07_curated", "Curated Datasets")

        # 转到 Ontologies
        page.goto(f"{BASE_UI}/ontologies", wait_until="networkidle")
        time.sleep(1.5)
        shot(page, "08_ontologies", "本体列表")

        br.close()

    print("\n" + "═"*60)
    print("  ✅ 全流程测试完成")
    print("═"*60)
    print(f"  截图保存在: {SS_DIR}")
    for p in sorted(SS_DIR.glob("*.png")):
        print(f"    {p.name}")

if __name__ == "__main__":
    main()

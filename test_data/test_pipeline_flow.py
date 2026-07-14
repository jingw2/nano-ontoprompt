#!/usr/bin/env python3
"""
验证 Pipeline 全流程：上传 → Pipeline → Curated Dataset → Ontology
确保每个步骤都实际产生数据
"""
import requests, json, time, os
from pathlib import Path

BASE_API = "http://localhost:8000"
TEST_DATA = Path(__file__).parent / "供应链"

session = requests.Session()

def api(method, path, **kwargs):
    r = session.request(method, f"{BASE_API}{path}", **kwargs)
    return r

# Login
r = api("POST", "/api/v1/auth/login", json={"username":"admin","password":"admin123"})
token = r.json()["data"]["access_token"]
session.headers.update({"Authorization": f"Bearer {token}"})
print("✅ 登录成功")

# 清理旧数据：创建唯一名称避免冲突
ts = int(time.time())

# 1. 上传一个 CSV 文件（确保有数据行）
print("\n── 1. 上传 CSV 测试数据 ──")
csv_content = b"id,name,amount,order_date\n1,Alice,100,2024-01-01\n2,Bob,200,2024-01-02\n3,Charlie,300,2024-01-03\n4,Diana,400,2024-01-04\n5,Eve,500,2024-01-05\n"
r = api("POST", "/api/v2/datasets/upload", files={"file": ("test_orders.csv", csv_content, "text/csv")})
ds_data = r.json()
ds_id = ds_data.get("data", {}).get("id") if isinstance(ds_data, dict) else None
if not ds_id and isinstance(ds_data, dict):
    ds_id = ds_data.get("id")
print(f"数据集创建: {r.status_code} id={ds_id}")

# 2. 验证数据集有数据
r = api("GET", f"/api/v2/datasets/{ds_id}/versions")
versions = r.json() if r.ok else []
print(f"版本: {versions}")
if versions:
    vno = versions[0]["version_no"]
    r2 = api("GET", f"/api/v2/datasets/{ds_id}/versions/{vno}/preview")
    print(f"数据预览 ({r2.status_code}): {r2.text[:200]}")

# 3. 创建 Pipeline 指向该数据集
print("\n── 2. 创建 Pipeline ──")
pl_name = f"E2E-验证流水线-{ts}"
r = api("POST", "/api/v2/pipelines", json={
    "name": pl_name,
    "domain": "供应链",
    "description": "E2E 验证 Pipeline",
    "source_dataset_id": ds_id,
    "route": "A",
    "spec": {"steps": [{"op": "drop_duplicates", "columns": ["id"]}]},
})
pl = r.json() if r.ok else {}
pl_id = pl.get("id", "")
print(f"Pipeline: {r.status_code} id={pl_id[:8] if pl_id else '?'}")

# 4. 运行 Pipeline（同步）
print("\n── 3. 运行 Pipeline ──")
r = api("POST", f"/api/v2/pipelines/{pl_id}/run-sync")
print(f"运行结果: {r.status_code}")
result = r.json() if r.ok else {}
print(f"  status: {result.get('status')}")
stats = result.get("stats", {})
print(f"  rows_in: {stats.get('rows_in')}")
print(f"  rows_out: {stats.get('rows_out')}")
curated_id = stats.get("curated_dataset_id")
print(f"  curated_dataset_id: {curated_id}")

if not curated_id:
    print("❌ Pipeline 未生成 Curated Dataset!")
    exit(1)

# 5. 验证 Curated Dataset 有数据
print("\n── 4. 验证 Curated Dataset ──")
r = api("GET", f"/api/v2/curated")
curated_list = r.json() if r.ok else []
curated_list = curated_list if isinstance(curated_list, list) else []
print(f"Curated Datasets: {len(curated_list)} 个")

cd = next((c for c in curated_list if c.get("id") == curated_id), None)
if cd:
    print(f"  name: {cd.get('name')}")
    print(f"  status: {cd.get('status')}")
    # 直接查看数据
    r2 = api("GET", f"/api/v2/datasets/{curated_id}/versions")
    vers = r2.json() if r2.ok else []
    if vers:
        v = vers[0]["version_no"]
        r3 = api("GET", f"/api/v2/datasets/{curated_id}/versions/{v}/preview")
        print(f"  data: {r3.text[:200]}")

# 6. 审批 Curated Dataset
print("\n── 5. 审批 Curated Dataset ──")
r = api("POST", f"/api/v2/curated/{curated_id}/review", json={"status": "approved"})
print(f"审批: {r.status_code}")

# 7. 创建 Ontology (Pipeline Mapping)
print("\n── 6. 创建 Ontology ──")
onto_name = f"E2E-验证本体-{ts}"
r = api("POST", "/api/v1/ontologies", json={
    "name": onto_name,
    "domain": "供应链",
    "description": "E2E 验证本体",
    "build_mode": "pipeline_mapping",
})
onto_data = r.json() if r.ok else {}
onto_id = onto_data.get("data", onto_data).get("id") if isinstance(onto_data, dict) else None
if not onto_id:
    onto_id = onto_data.get("id")
print(f"Ontology: {r.status_code} id={onto_id[:8] if onto_id else '?'}")

if onto_id:
    # 创建 Mapping
    r = api("POST", f"/api/v2/ontologies/{onto_id}/mappings", json={
        "curated_dataset_id": curated_id,
        "entity_class": "TestOrder",
        "field_mapping": {"__primary_key__": "id", "name": "customer_name", "amount": "order_amount"},
    })
    mapping_id = r.json().get("mapping_id", "") if r.ok else ""
    print(f"Mapping: {r.status_code} id={mapping_id[:8] if mapping_id else '?'}")

    if mapping_id:
        # Apply from dataset
        r = api("POST", f"/api/v2/ontologies/{onto_id}/mappings/{mapping_id}/apply-from-dataset")
        print(f"Apply: {r.status_code} {r.text[:200]}")

        # Build-all
        r = api("POST", f"/api/v2/ontologies/{onto_id}/mappings/build-all")
        print(f"Build-all: {r.status_code} {r.text[:200]}")

        # 检查实体
        r = api("GET", f"/api/v2/ontologies/{onto_id}/entities")
        entities = r.json() if r.ok else []
        print(f"实体数量: {len(entities) if isinstance(entities, list) else '?'}")

print("\n" + "═"*50)
print("✅ 管道: rows_in={} → rows_out={}".format(
    stats.get("rows_in", "?"), stats.get("rows_out", "?")))
print("✅ Curated: {}".format(curated_id[:8] if curated_id else "N/A"))
if onto_id:
    print("✅ Ontology: {} (实体: {})".format(
        onto_id[:8],
        len(entities) if isinstance(entities, list) and entities else "0"))
print("═"*50)

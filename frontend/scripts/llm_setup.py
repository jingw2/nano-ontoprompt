"""Pre-setup: create ontology and upload full supply-chain dataset, print JSON result."""
import requests, json, time, sys, os

BASE = "http://localhost:8002/api/v1"
DATA = os.path.join(os.path.dirname(__file__), "..", "test_data", "documents")

r = requests.post(f"{BASE}/auth/login", json={"username": "admin", "password": "admin123"})
token = r.json()["data"]["access_token"]
H = {"Authorization": f"Bearer {token}"}

name = f"供应链全链路知识图谱-{int(time.time())}"
r2 = requests.post(f"{BASE}/ontologies", json={
    "name": name, "domain": "供应链",
    "description": "覆盖采购→生产→仓储→物流履约全链路，多源多格式数据，由DeepSeek自动提取"
}, headers=H)
oid = r2.json()["data"]["id"]

mime_map = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".md":   "text/markdown",
    ".csv":  "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

# Full supply chain dataset — 7 files across 4 formats
files_to_upload = [
    "procurement_management.docx",   # 采购管理制度 (DOCX)
    "purchase_orders.csv",           # 采购订单明细 (CSV)
    "production_management.md",      # 生产计划与BOM (MD)
    "warehouse_inventory.xlsx",      # 仓储库存数据 (XLSX)
    "logistics_fulfillment.csv",     # 物流履约记录 (CSV)
    "supply_chain_full_spec.docx",   # 供应链综合规范 (DOCX)
    "quality_control.csv",           # 质检数据 (CSV)
]

files_uploaded = []
for fname in files_to_upload:
    fpath = os.path.join(DATA, fname)
    if not os.path.exists(fpath):
        sys.stderr.write(f"Missing: {fpath}\n")
        continue
    ext = os.path.splitext(fname)[1]
    with open(fpath, "rb") as f:
        content = f.read()
    r3 = requests.post(f"{BASE}/ontologies/{oid}/files",
        headers=H, files={"file": (fname, content, mime_map[ext])})
    if r3.ok:
        files_uploaded.append(fname)
        sys.stderr.write(f"  ✓ {fname}\n")
    else:
        sys.stderr.write(f"  ✗ {fname}: {r3.status_code} {r3.text[:100]}\n")

models = requests.get(f"{BASE}/models", headers=H).json()["data"]
model = next(m for m in models if "DeepSeek" in m["name"])
prompts_list = requests.get(f"{BASE}/prompts", headers=H).json()["data"]
prompt = next(p for p in prompts_list if p["name"] == "供应链本体提取")

print(json.dumps({
    "oid": oid, "name": name,
    "model_id": model["id"], "prompt_id": prompt["id"],
    "files": files_uploaded,
}))

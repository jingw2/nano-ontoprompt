"""
Seed a rich supply-chain knowledge graph ontology so the graph tab shows
a proper network of nodes and edges.  Also registers the DeepSeek model.
Run from the repo root: python seed_graph.py
"""
import requests, sys

BASE = "http://localhost:8000/api/v1"

# ── Auth ──────────────────────────────────────────────────────────────────
r = requests.post(f"{BASE}/auth/login", json={"username": "admin", "password": "admin123"})
r.raise_for_status()
token = r.json()["data"]["access_token"]
H = {"Authorization": f"Bearer {token}"}
print("✓ Logged in")

# ── Create ontology ───────────────────────────────────────────────────────
r = requests.post(f"{BASE}/ontologies", json={
    "name": "供应链知识图谱-演示",
    "domain": "供应链",
    "description": "包含供应商、产品、采购订单、合同、仓储、物流等完整供应链实体及关系"
}, headers=H)
r.raise_for_status()
oid = r.json()["data"]["id"]
print(f"✓ Created ontology {oid}")

# ── Entities ──────────────────────────────────────────────────────────────
entities_spec = [
    ("华为技术有限公司",  "Huawei Technologies",    "Organization", "主要芯片供应商",       0.98),
    ("三星显示公司",      "Samsung Display",        "Organization", "OLED屏幕供应商",       0.97),
    ("苹果公司",          "Apple Inc.",             "Organization", "终端采购商与品牌方",   0.99),
    ("iPhone 16",         "iPhone 16",              "Product",      "终端智能手机产品",     0.96),
    ("A18芯片",           "A18 Chip",               "Product",      "最新一代移动处理器",   0.95),
    ("OLED柔性屏幕",      "OLED Flexible Display",  "Product",      "6.1英寸高刷新率屏幕",  0.94),
    ("采购订单PO-2024",   "Purchase Order PO-2024", "Document",     "年度芯片采购订单",     0.92),
    ("供应合同2024",      "Supply Contract 2024",   "Document",     "三年期独家供应合同",   0.93),
    ("上海保税仓",        "Shanghai Bonded Warehouse","Facility",   "保税区主仓储中心",     0.91),
    ("深圳生产基地",      "Shenzhen Production Base","Facility",    "芯片封测与组装基地",   0.90),
    ("顺丰物流",          "SF Express",             "Organization", "干线物流与配送服务商", 0.89),
    ("上海海关",          "Shanghai Customs",       "Organization", "进出口海关监管机构",   0.88),
    ("质检报告QC-001",    "QC Report QC-001",       "Document",     "A18芯片出货质检报告",  0.87),
    ("发票INV-2024-001",  "Invoice INV-2024-001",   "Document",     "芯片采购结算发票",     0.86),
]

entity_ids = {}
for name_cn, name_en, etype, desc, conf in entities_spec:
    r = requests.post(f"{BASE}/ontologies/{oid}/entities", json={
        "name_cn": name_cn, "name_en": name_en,
        "type": etype, "description": desc, "confidence": conf
    }, headers=H)
    r.raise_for_status()
    entity_ids[name_cn] = r.json()["data"]["id"]
    print(f"  + entity: {name_cn}")

print(f"✓ Created {len(entity_ids)} entities")

# ── Relations (source → target) ───────────────────────────────────────────
relations_spec = [
    # supply chain flows
    ("华为技术有限公司",   "A18芯片",           "供货",    0.97),
    ("三星显示公司",       "OLED柔性屏幕",      "供货",    0.96),
    ("苹果公司",           "iPhone 16",         "采购",    0.98),
    # product composition
    ("iPhone 16",          "A18芯片",           "包含",    0.99),
    ("iPhone 16",          "OLED柔性屏幕",      "包含",    0.99),
    # document → product
    ("采购订单PO-2024",    "A18芯片",           "订购",    0.93),
    ("采购订单PO-2024",    "OLED柔性屏幕",      "订购",    0.92),
    # contract governance
    ("供应合同2024",       "采购订单PO-2024",   "约束",    0.94),
    ("华为技术有限公司",   "供应合同2024",      "签署",    0.95),
    ("苹果公司",           "供应合同2024",      "签署",    0.95),
    # warehouse & logistics
    ("上海保税仓",         "iPhone 16",         "存储",    0.90),
    ("深圳生产基地",       "A18芯片",           "生产",    0.92),
    ("顺丰物流",           "iPhone 16",         "配送",    0.88),
    ("顺丰物流",           "上海保税仓",        "入库",    0.87),
    # customs & QC
    ("上海海关",           "上海保税仓",        "监管",    0.86),
    ("质检报告QC-001",     "A18芯片",           "验证",    0.91),
    # invoice
    ("发票INV-2024-001",   "采购订单PO-2024",   "结算",    0.89),
    ("苹果公司",           "发票INV-2024-001",  "开具",    0.88),
]

for src_name, tgt_name, rel_type, conf in relations_spec:
    src_id = entity_ids[src_name]
    tgt_id = entity_ids[tgt_name]
    r = requests.post(f"{BASE}/ontologies/{oid}/graph/relations", json={
        "source_entity": src_id,
        "target_entity": tgt_id,
        "type": rel_type,
        "confidence": conf,
    }, headers=H)
    r.raise_for_status()
    print(f"  ↔ {src_name} --[{rel_type}]--> {tgt_name}")

print(f"✓ Created {len(relations_spec)} relations")

# ── Add logic rules ───────────────────────────────────────────────────────
rules = [
    ("采购触发规则",    "PurchaseTriggerRule",    "IF 库存量 < 最小库存 THEN 生成采购订单",       0.90),
    ("质检通过规则",    "QCPassRule",             "IF 质检合格率 >= 0.99 THEN 批准发货",           0.95),
    ("合规审查规则",    "ComplianceCheckRule",    "IF 供应商等级 < B THEN 需要额外审批",           0.85),
    ("成本预警规则",    "CostAlertRule",          "IF 单价涨幅 > 10% THEN 触发成本预警",           0.88),
]
for name_cn, name_en, formula, conf in rules:
    requests.post(f"{BASE}/ontologies/{oid}/logic", json={
        "name_cn": name_cn, "name_en": name_en,
        "formula": formula, "confidence": conf
    }, headers=H)
print("✓ Created logic rules")

print(f"\n📊 Ontology ID: {oid}")
print(f"   Open: http://localhost:5173/ontologies/{oid}")

# ── Register DeepSeek model (API key 从环境变量读取, 不要硬编码) ──────────
import os
r = requests.post(f"{BASE}/models", json={
    "name": "DeepSeek V4 Flash",
    "provider": "compatible",
    "api_key": os.getenv("DEEPSEEK_API_KEY", ""),
    "api_base": "https://api.deepseek.com/v1",
    "models": ["deepseek-chat", "deepseek-v4-flash", "deepseek-reasoner"],
}, headers=H)
if r.ok:
    mid = r.json()["data"]["id"]
    print(f"\n🤖 DeepSeek model registered (id={mid})")
else:
    print(f"\n⚠ DeepSeek model registration: {r.text[:200]}")

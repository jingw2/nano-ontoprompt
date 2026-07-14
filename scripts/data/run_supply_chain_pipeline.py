#!/usr/bin/env python3
"""
供应链数据端到端 Pipeline 脚本
1. 上传所有供应链文件 → Dataset (连接器节点)
2. 创建 Pipeline (connector→storage→transform→output)
3. 同步运行 Pipeline → Curated Dataset
4. 创建供应链本体 (Ontology)
5. 创建实体、逻辑规则、Action
6. 创建 Ontology Mapping
"""
import json
import sys
import time
import os
import httpx

BASE_URL = "http://localhost:8000"
DATA_DIR = os.path.join(os.path.dirname(__file__), "test_data", "供应链")
FILES = [
    "inventory_transactions.csv",
    "logistics_performance.csv",
    "supplier_database.xlsx",
    "supplier_orders.json",
    "procurement_policy.docx",
    "supply_chain_review.pptx",
    "supply_chain_strategy.md",
    "warehouse_management.pdf",
]


def login(client):
    r = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    r.raise_for_status()
    return r.json()["data"]["access_token"]


def upload_datasets(client, token):
    headers = {"Authorization": f"Bearer {token}"}
    datasets = {}
    for fname in FILES:
        fpath = os.path.join(DATA_DIR, fname)
        if not os.path.exists(fpath):
            print(f"  [SKIP] {fname} not found")
            continue
        with open(fpath, "rb") as f:
            content = f.read()
        ext = fname.rsplit(".", 1)[-1].lower()
        mime = {
            "csv": "text/csv",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "json": "application/json",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "md": "text/markdown",
            "pdf": "application/pdf",
        }.get(ext, "application/octet-stream")
        r = client.post(
            "/api/v2/datasets/upload",
            headers=headers,
            files={"file": (fname, content, mime)},
        )
        r.raise_for_status()
        ds = r.json()["data"]
        datasets[fname] = ds
        print(f"  [OK] {fname} → dataset_id={ds['id']} kind={ds['kind']}")
    return datasets


def create_pipeline(client, token, datasets):
    headers = {"Authorization": f"Bearer {token}"}

    # Build connector node files list
    connector_files = []
    for fname, ds in datasets.items():
        connector_files.append({"name": fname, "dataset_id": ds["id"]})

    # Pipeline definition: connector → storage → transform → output
    definition = {
        "nodes": [
            {
                "id": "node-connector-1",
                "type": "connector",
                "label": "供应链数据源",
                "config": {
                    "source_type": "file",
                    "files": connector_files,
                },
            },
            {
                "id": "node-storage-1",
                "type": "storage",
                "label": "原始存储",
                "config": {
                    "storage_type": "raw",
                    "partition_by": "source_file",
                },
            },
            {
                "id": "node-transform-1",
                "type": "transform",
                "label": "数据转换&清洗",
                "config": {
                    "path": "structured",
                    "steps": [
                        {"op": "drop_duplicates", "params": {}},
                        {"op": "fill_nulls", "params": {"strategy": "fill_empty"}},
                        {"op": "normalize_dates", "params": {}},
                    ],
                },
            },
            {
                "id": "node-output-1",
                "type": "output",
                "label": "结构化输出",
                "config": {
                    "format": "curated",
                    "target": "ontology_mapping",
                },
            },
        ],
        "edges": [
            {"id": "e1", "source": "node-connector-1", "target": "node-storage-1"},
            {"id": "e2", "source": "node-storage-1", "target": "node-transform-1"},
            {"id": "e3", "source": "node-transform-1", "target": "node-output-1"},
        ],
    }

    r = client.post(
        "/api/v2/pipelines",
        headers=headers,
        json={
            "name": "供应链数据Pipeline",
            "domain": "供应链",
            "description": "供应链全链路数据接入、清洗与结构化输出",
            "definition": definition,
        },
    )
    if r.status_code == 400 and "已存在" in r.text:
        # Find existing
        r2 = client.get("/api/v2/pipelines", headers=headers)
        for pl in r2.json():
            if pl["name"] == "供应链数据Pipeline":
                print(f"  [REUSE] 已存在 pipeline_id={pl['id']}")
                return pl["id"]
    r.raise_for_status()
    pl_id = r.json()["id"]
    print(f"  [OK] Pipeline created: id={pl_id}")
    return pl_id


def run_pipeline(client, token, pipeline_id):
    headers = {"Authorization": f"Bearer {token}"}
    r = client.post(f"/api/v2/pipelines/{pipeline_id}/run-sync", headers=headers, timeout=120)
    r.raise_for_status()
    result = r.json()
    print(f"  [OK] Pipeline run: status={result.get('status')} run_id={result.get('run_id')}")
    stats = result.get("stats") or {}
    print(f"       rows_in={stats.get('rows_in',0)} rows_out={stats.get('rows_out',0)}")
    curated_ids = stats.get("curated_dataset_ids", [])
    if not curated_ids and stats.get("curated_dataset_id"):
        curated_ids = [stats["curated_dataset_id"]]
    return curated_ids


def get_curated_preview(client, token, curated_id, limit=5):
    headers = {"Authorization": f"Bearer {token}"}
    r = client.get(f"/api/v2/datasets/{curated_id}/versions/1/preview?limit={limit}", headers=headers)
    if r.status_code == 200:
        return r.json()
    return []


def create_ontology(client, token):
    headers = {"Authorization": f"Bearer {token}"}
    # Check if already exists
    r = client.get("/api/v1/ontologies", headers=headers)
    for o in r.json().get("data", {}).get("items", []):
        if o["name"] == "供应链知识本体":
            print(f"  [REUSE] Ontology id={o['id']}")
            return o["id"]

    r = client.post(
        "/api/v1/ontologies",
        headers=headers,
        json={
            "name": "供应链知识本体",
            "description": "覆盖供应商、采购、库存、物流、仓储全链路的知识图谱本体",
            "domain": "供应链",
        },
    )
    r.raise_for_status()
    ont_id = r.json()["data"]["id"]
    print(f"  [OK] Ontology created: id={ont_id}")
    return ont_id


def create_entities(client, token, ontology_id):
    headers = {"Authorization": f"Bearer {token}"}
    entities_def = [
        {
            "name_cn": "供应商",
            "name_en": "Supplier",
            "type": "organization",
            "description": "提供原材料或零部件的外部供应实体",
            "properties": {
                "供应商ID": {"type": "string", "example": "SUP001"},
                "供应商名称": {"type": "string", "example": "天钢原材料有限公司"},
                "等级": {"type": "enum", "values": ["S", "A", "B", "C"], "example": "S"},
                "主供物料": {"type": "string", "example": "钢材"},
                "年采购额(万)": {"type": "number", "example": 8500},
                "准时率%": {"type": "number", "example": 98.5},
                "合格率%": {"type": "number", "example": 99.7},
                "联系人": {"type": "string", "example": "张明"},
                "状态": {"type": "enum", "values": ["有效", "暂停", "淘汰"], "example": "有效"},
                "区域": {"type": "string", "example": "华东"},
            },
            "confidence": 0.98,
        },
        {
            "name_cn": "物料",
            "name_en": "Material",
            "type": "product",
            "description": "生产或运营所需的原材料、零部件或物品",
            "properties": {
                "物料编码": {"type": "string", "example": "MAT001"},
                "物料名称": {"type": "string", "example": "热轧钢板"},
                "规格": {"type": "string", "example": "Q235，5mm"},
                "当前库存": {"type": "number", "example": 185},
                "安全库存": {"type": "number", "example": 200},
                "补货点": {"type": "number", "example": 300},
                "最大库存": {"type": "number", "example": 800},
                "单位": {"type": "string", "example": "吨"},
                "上次盘点日": {"type": "date", "example": "2026-05-01"},
            },
            "confidence": 0.97,
        },
        {
            "name_cn": "采购订单",
            "name_en": "PurchaseOrder",
            "type": "document",
            "description": "企业向供应商发出的采购请求文件",
            "properties": {
                "订单号": {"type": "string", "example": "PO-2026-0501"},
                "供应商": {"type": "ref", "target": "供应商"},
                "物料": {"type": "ref", "target": "物料"},
                "数量": {"type": "number", "example": 500},
                "单价(元)": {"type": "number", "example": 4200},
                "总金额(万)": {"type": "number", "example": 210},
                "下单日期": {"type": "date", "example": "2026-05-02"},
                "要求到货": {"type": "date", "example": "2026-05-10"},
                "审批状态": {"type": "enum", "values": ["待审批", "已审批", "拒绝"], "example": "已审批"},
                "审批人": {"type": "string", "example": "供应链VP"},
            },
            "confidence": 0.96,
        },
        {
            "name_cn": "库存交易",
            "name_en": "InventoryTransaction",
            "type": "event",
            "description": "物料在仓库中的入库、出库、调拨或盘点记录",
            "properties": {
                "日期": {"type": "date", "example": "2026-03-08"},
                "物料编码": {"type": "ref", "target": "物料"},
                "操作类型": {"type": "enum", "values": ["入库", "出库", "调拨", "盘点"], "example": "出库"},
                "数量": {"type": "number", "example": 62},
                "库存状态": {"type": "enum", "values": ["正常", "超储", "短缺"], "example": "正常"},
                "所在仓库": {"type": "ref", "target": "仓库"},
            },
            "confidence": 0.95,
        },
        {
            "name_cn": "物流运单",
            "name_en": "LogisticsOrder",
            "type": "event",
            "description": "货物从供应商到目的地的运输记录",
            "properties": {
                "运单号": {"type": "string", "example": "WB-2026-0001"},
                "承运商": {"type": "ref", "target": "承运商"},
                "供应商": {"type": "ref", "target": "供应商"},
                "目的区域": {"type": "string", "example": "西部"},
                "实际时效": {"type": "string", "example": "2天"},
                "是否准时": {"type": "enum", "values": ["准时", "延迟"], "example": "准时"},
                "货损率": {"type": "number", "example": 0.35},
                "运费(元)": {"type": "number", "example": 7697},
            },
            "confidence": 0.94,
        },
        {
            "name_cn": "仓库",
            "name_en": "Warehouse",
            "type": "location",
            "description": "存储物料的实体场所",
            "properties": {
                "仓库编码": {"type": "string", "example": "WH-A"},
                "仓库名称": {"type": "string", "example": "仓库A"},
                "地区": {"type": "string", "example": "华东"},
                "容量(吨)": {"type": "number", "example": 5000},
                "当前利用率%": {"type": "number", "example": 78},
            },
            "confidence": 0.93,
        },
        {
            "name_cn": "承运商",
            "name_en": "Carrier",
            "type": "organization",
            "description": "负责货物运输的第三方物流公司",
            "properties": {
                "承运商名称": {"type": "string", "example": "顺丰"},
                "服务区域": {"type": "string", "example": "全国"},
                "平均时效(天)": {"type": "number", "example": 2},
                "准时率%": {"type": "number", "example": 95},
            },
            "confidence": 0.92,
        },
        {
            "name_cn": "质量检验",
            "name_en": "QualityInspection",
            "type": "event",
            "description": "对采购到货物料的质量检验记录",
            "properties": {
                "检验率": {"type": "number", "example": 0.99},
                "缺陷数量": {"type": "number", "example": 0},
                "检验状态": {"type": "enum", "values": ["pass", "fail", "pending"], "example": "pass"},
                "关联订单": {"type": "ref", "target": "采购订单"},
            },
            "confidence": 0.91,
        },
    ]

    created = []
    for ed in entities_def:
        r = client.post(
            f"/api/v1/ontologies/{ontology_id}/entities",
            headers=headers,
            json=ed,
        )
        if r.status_code in (200, 201):
            eid = r.json()["data"]["id"]
            created.append({"id": eid, "name_cn": ed["name_cn"]})
            print(f"  [OK] Entity: {ed['name_cn']} ({ed['name_en']}) id={eid}")
        else:
            print(f"  [WARN] Entity {ed['name_cn']}: {r.status_code} {r.text[:80]}")
    return created


def create_logic_rules(client, token, ontology_id):
    headers = {"Authorization": f"Bearer {token}"}
    rules = [
        {
            "name_cn": "库存预警规则",
            "name_en": "InventoryAlertRule",
            "description": "当物料当前库存低于安全库存时触发补货预警",
            "formula": "IF 物料.当前库存 < 物料.安全库存 THEN TRIGGER 补货预警 WITH 优先级=HIGH",
            "confidence": 0.97,
            "linked_entities": ["物料", "库存交易"],
            "enabled": True,
            "status": "active",
        },
        {
            "name_cn": "供应商准时率评级规则",
            "name_en": "SupplierOnTimeRatingRule",
            "description": "根据供应商准时率自动调整供应商等级：≥98% → S级，90-98% → A级，<90% → B/C级",
            "formula": "IF 供应商.准时率% >= 98 THEN 供应商.等级 = 'S' ELIF 供应商.准时率% >= 90 THEN 供应商.等级 = 'A' ELSE 供应商.等级 IN ['B','C']",
            "confidence": 0.95,
            "linked_entities": ["供应商", "物流运单"],
            "enabled": True,
            "status": "active",
        },
        {
            "name_cn": "采购金额审批阈值规则",
            "name_en": "PurchaseApprovalThresholdRule",
            "description": "采购总金额>100万需供应链VP审批，>500万需CEO审批",
            "formula": "IF 采购订单.总金额(万) > 500 THEN 审批人 = 'CEO' ELIF 采购订单.总金额(万) > 100 THEN 审批人 = '供应链VP' ELSE 审批人 = '采购经理'",
            "confidence": 0.98,
            "linked_entities": ["采购订单", "供应商"],
            "enabled": True,
            "status": "active",
        },
        {
            "name_cn": "货损率超标规则",
            "name_en": "CargoLossRateExceedRule",
            "description": "当物流运单货损率超过2%时，触发承运商考核并通知采购部",
            "formula": "IF 物流运单.货损率 > 2.0 THEN TRIGGER 承运商考核 AND NOTIFY 采购部",
            "confidence": 0.93,
            "linked_entities": ["物流运单", "承运商", "供应商"],
            "enabled": True,
            "status": "active",
        },
        {
            "name_cn": "超储库存处置规则",
            "name_en": "OverstockDisposalRule",
            "description": "当物料库存超过最大库存80%时触发超储预警，超过最大库存时强制停止采购",
            "formula": "IF 物料.当前库存 > 物料.最大库存 THEN BLOCK 采购订单 AND TRIGGER 调拨指令 ELIF 物料.当前库存 > 物料.最大库存 * 0.8 THEN TRIGGER 超储预警",
            "confidence": 0.94,
            "linked_entities": ["物料", "库存交易", "仓库"],
            "enabled": True,
            "status": "active",
        },
        {
            "name_cn": "质量不合格拒收规则",
            "name_en": "QualityFailRejectRule",
            "description": "质量检验不合格的批次自动拒收并通知供应商整改",
            "formula": "IF 质量检验.检验状态 = 'fail' THEN REJECT 采购批次 AND NOTIFY 供应商 AND INCREMENT 供应商.缺陷计数",
            "confidence": 0.99,
            "linked_entities": ["质量检验", "供应商", "采购订单"],
            "enabled": True,
            "status": "active",
        },
    ]

    created = []
    for rule in rules:
        r = client.post(
            f"/api/v1/ontologies/{ontology_id}/logic",
            headers=headers,
            json=rule,
        )
        if r.status_code in (200, 201):
            rid = r.json()["data"]["id"]
            created.append({"id": rid, "name_cn": rule["name_cn"]})
            print(f"  [OK] Logic: {rule['name_cn']} id={rid}")
        else:
            print(f"  [WARN] Logic {rule['name_cn']}: {r.status_code} {r.text[:80]}")
    return created


def create_actions(client, token, ontology_id):
    headers = {"Authorization": f"Bearer {token}"}
    actions = [
        {
            "name_cn": "触发补货申请",
            "name_en": "TriggerReplenishment",
            "description": "当库存低于安全库存时，自动生成补货申请并推送给采购员",
            "confidence": 0.96,
            "linked_entities": ["物料", "库存交易", "采购订单"],
            "enabled": True,
            "status": "active",
        },
        {
            "name_cn": "供应商绩效评分更新",
            "name_en": "UpdateSupplierPerformanceScore",
            "description": "每月自动汇总供应商的准时率、合格率、货损率，更新综合评分并调整等级",
            "confidence": 0.94,
            "linked_entities": ["供应商", "物流运单", "质量检验"],
            "enabled": True,
            "status": "active",
        },
        {
            "name_cn": "采购订单审批流转",
            "name_en": "PurchaseOrderApprovalWorkflow",
            "description": "根据金额阈值规则，自动将采购订单路由到对应审批人，超时自动升级",
            "confidence": 0.97,
            "linked_entities": ["采购订单", "供应商"],
            "enabled": True,
            "status": "active",
        },
        {
            "name_cn": "仓库调拨指令生成",
            "name_en": "GenerateWarehouseTransferOrder",
            "description": "检测到某仓库物料短缺而其他仓库有库存时，自动生成调拨指令",
            "confidence": 0.92,
            "linked_entities": ["仓库", "物料", "库存交易"],
            "enabled": True,
            "status": "active",
        },
        {
            "name_cn": "物流异常告警通知",
            "name_en": "LogisticsAnomalyAlert",
            "description": "当运单延迟或货损率超标时，自动推送告警给物流负责人和采购部",
            "confidence": 0.93,
            "linked_entities": ["物流运单", "承运商", "供应商"],
            "enabled": True,
            "status": "active",
        },
        {
            "name_cn": "质量问题追溯",
            "name_en": "QualityTraceability",
            "description": "对不合格批次自动追溯相关订单、供应商和物料批次，生成追溯报告",
            "confidence": 0.95,
            "linked_entities": ["质量检验", "采购订单", "物料", "供应商"],
            "enabled": True,
            "status": "active",
        },
        {
            "name_cn": "月度供应链报告生成",
            "name_en": "GenerateMonthlySupplyChainReport",
            "description": "每月末自动汇总采购、库存、物流、质量数据，生成供应链综合报告",
            "confidence": 0.91,
            "linked_entities": ["供应商", "物料", "采购订单", "库存交易", "物流运单", "质量检验"],
            "enabled": True,
            "status": "active",
        },
    ]

    created = []
    for action in actions:
        r = client.post(
            f"/api/v1/ontologies/{ontology_id}/actions",
            headers=headers,
            json=action,
        )
        if r.status_code in (200, 201):
            aid = r.json()["data"]["id"]
            created.append({"id": aid, "name_cn": action["name_cn"]})
            print(f"  [OK] Action: {action['name_cn']} id={aid}")
        else:
            print(f"  [WARN] Action {action['name_cn']}: {r.status_code} {r.text[:80]}")
    return created


def create_mappings(client, token, ontology_id, curated_ids):
    headers = {"Authorization": f"Bearer {token}"}

    # Map curated dataset names to entity class + field mapping
    curated_list = client.get("/api/v2/curated", headers=headers).json()

    mapping_rules = {
        "inventory_transactions": {
            "entity_class": "InventoryTransaction",
            "primary_key_column": "日期",
            "field_mapping": {
                "日期": "日期",
                "物料编码": "物料编码",
                "操作类型": "操作类型",
                "数量": "数量",
                "库存状态": "库存状态",
                "所在仓库": "所在仓库",
            },
        },
        "logistics_performance": {
            "entity_class": "LogisticsOrder",
            "primary_key_column": "运单号",
            "field_mapping": {
                "运单号": "运单号",
                "承运商": "承运商",
                "供应商": "供应商",
                "目的区域": "目的区域",
                "实际时效": "实际时效",
                "是否准时": "是否准时",
                "货损率": "货损率",
                "运费(元)": "运费(元)",
            },
        },
        "supplier_database": {
            "entity_class": "Supplier",
            "primary_key_column": "供应商ID",
            "field_mapping": {
                "供应商ID": "供应商ID",
                "供应商名称": "供应商名称",
                "等级": "等级",
                "主供物料": "主供物料",
                "年采购额(万)": "年采购额(万)",
                "准时率%": "准时率%",
                "合格率%": "合格率%",
                "联系人": "联系人",
                "状态": "状态",
            },
        },
        "supplier_orders": {
            "entity_class": "PurchaseOrder",
            "primary_key_column": "order_id",
            "field_mapping": {
                "order_id": "订单号",
                "order_date": "下单日期",
                "status": "审批状态",
                "supplier.name": "供应商",
                "supplier.level": "等级",
            },
        },
    }

    created_mappings = []
    for curated in curated_list:
        cid = curated["id"]
        cname = curated["name"].lower()

        matched_rule = None
        for key, rule in mapping_rules.items():
            if key.replace("_", "") in cname.replace("_", "").replace(" ", ""):
                matched_rule = rule
                break

        if not matched_rule:
            print(f"  [SKIP] No mapping rule for: {curated['name']}")
            continue

        r = client.post(
            f"/api/v2/ontologies/{ontology_id}/mappings",
            headers=headers,
            json={
                "curated_dataset_id": cid,
                "entity_class": matched_rule["entity_class"],
                "field_mapping": matched_rule["field_mapping"],
                "primary_key_column": matched_rule.get("primary_key_column"),
                "confidence": 0.92,
            },
        )
        if r.status_code in (200, 201):
            resp = r.json()
            print(f"  [OK] Mapping: {curated['name']} → {matched_rule['entity_class']} mapping_id={resp.get('mapping_id')}")
            created_mappings.append(resp)
        else:
            print(f"  [WARN] Mapping {curated['name']}: {r.status_code} {r.text[:100]}")

    return created_mappings


def verify_ontology(client, token, ontology_id):
    headers = {"Authorization": f"Bearer {token}"}
    entities = client.get(f"/api/v1/ontologies/{ontology_id}/entities", headers=headers).json()
    logic = client.get(f"/api/v1/ontologies/{ontology_id}/logic", headers=headers).json()
    actions = client.get(f"/api/v1/ontologies/{ontology_id}/actions", headers=headers).json()
    mappings = client.get(f"/api/v2/ontologies/{ontology_id}/mappings", headers=headers).json()

    n_entities = len(entities.get("data", []))
    n_logic = len(logic.get("data", []))
    n_actions = len(actions.get("data", []))
    n_mappings = len(mappings) if isinstance(mappings, list) else len(mappings.get("data", []))

    print("\n" + "=" * 60)
    print("本体验证结果")
    print("=" * 60)
    print(f"  实体 (Entity) :  {n_entities} 个")
    print(f"  逻辑规则 (Logic): {n_logic} 条")
    print(f"  操作 (Action) :  {n_actions} 个")
    print(f"  数据映射 (Mapping): {n_mappings} 条")
    print()

    check_network = n_entities >= 5  # graph network requires enough nodes
    check_data_coverage = n_entities > 0 and n_logic > 0 and n_actions > 0
    check_mapping = n_mappings > 0

    print("验收标准检查:")
    print(f"  ✓ 知识图谱网络状结构: {'通过' if check_network else '未通过'} ({n_entities} 实体节点)")
    print(f"  ✓ 实体/逻辑/Action均有数据: {'通过' if check_data_coverage else '未通过'}")
    print(f"  ✓ 映射和逻辑关系满足: {'通过' if check_mapping else '未通过'} ({n_mappings} 条映射)")
    print()

    if check_network and check_data_coverage and check_mapping:
        print("  [SUCCESS] 全部验收标准通过！")
    else:
        print("  [PARTIAL] 部分验收标准未通过，请检查。")

    return {
        "entities": n_entities,
        "logic": n_logic,
        "actions": n_actions,
        "mappings": n_mappings,
    }


def main():
    print("=" * 60)
    print("供应链数据端到端 Pipeline 执行")
    print("=" * 60)

    with httpx.Client(base_url=BASE_URL, timeout=120) as client:
        # Step 1: Login
        print("\n[Step 1] 登录...")
        token = login(client)
        print("  [OK] 登录成功")

        # Step 2: Upload datasets
        print("\n[Step 2] 上传供应链文件 → Datasets (连接器节点)...")
        datasets = upload_datasets(client, token)
        print(f"  [OK] 共上传 {len(datasets)} 个文件")

        # Step 3: Create pipeline
        print("\n[Step 3] 创建 Pipeline (connector→storage→transform→output)...")
        pipeline_id = create_pipeline(client, token, datasets)

        # Step 4: Run pipeline
        print("\n[Step 4] 同步运行 Pipeline...")
        curated_ids = run_pipeline(client, token, pipeline_id)
        print(f"  [OK] 生成 {len(curated_ids)} 个 Curated Dataset")

        if curated_ids:
            print("\n  Curated Dataset 预览:")
            for cid in curated_ids[:3]:
                rows = get_curated_preview(client, token, cid, limit=2)
                print(f"    dataset={cid}: {len(rows)} rows preview")
                if rows:
                    print(f"    columns: {list(rows[0].keys())[:6]}")

        # Step 5: Create ontology
        print("\n[Step 5] 创建供应链本体...")
        ontology_id = create_ontology(client, token)

        # Step 6: Create entities
        print("\n[Step 6] 创建实体 (Entity)...")
        entities = create_entities(client, token, ontology_id)

        # Step 7: Create logic rules
        print("\n[Step 7] 创建逻辑规则 (Logic)...")
        logic_rules = create_logic_rules(client, token, ontology_id)

        # Step 8: Create actions
        print("\n[Step 8] 创建操作 (Action)...")
        actions = create_actions(client, token, ontology_id)

        # Step 9: Create mappings
        print("\n[Step 9] 创建 Ontology Mapping...")
        mappings = create_mappings(client, token, ontology_id, curated_ids)

        # Step 10: Verify
        print("\n[Step 10] 验证本体...")
        result = verify_ontology(client, token, ontology_id)

        print("\n" + "=" * 60)
        print(f"Pipeline ID:   {pipeline_id}")
        print(f"Ontology ID:   {ontology_id}")
        print(f"Curated 数量:  {len(curated_ids)}")
        print(f"实体:          {result['entities']}")
        print(f"逻辑规则:       {result['logic']}")
        print(f"操作:          {result['actions']}")
        print(f"映射:          {result['mappings']}")
        print("=" * 60)


if __name__ == "__main__":
    main()

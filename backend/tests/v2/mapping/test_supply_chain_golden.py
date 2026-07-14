from unittest.mock import patch

from app.models.action import Action
from app.models.entity import Entity
from app.models.logic import LogicRule
from app.models.ontology import OntologyProject
from app.models.relation import Relation
from app.models.v2.action import OntologyActionType
from app.models.v2.curated import CuratedDataset
from app.models.v2.logic import OntologyLogicRule
from app.models.v2.mapping import OntologyLinkMapping, OntologyMapping
from app.services.v2.mapping.mapping_service import MappingService


def _add_curated(db, name: str) -> CuratedDataset:
    ds = CuratedDataset(name=name, status="approved", quality_score=0.9)
    db.add(ds)
    db.commit()
    db.refresh(ds)
    return ds


def _add_mapping(db, ontology_id: str, dataset_id: str, entity_class: str, pk: str) -> OntologyMapping:
    mapping = OntologyMapping(
        ontology_id=ontology_id,
        curated_dataset_id=dataset_id,
        entity_class=entity_class,
        field_mapping={"__primary_key__": pk},
        status="draft",
        confidence=0.9,
    )
    db.add(mapping)
    db.commit()
    db.refresh(mapping)
    return mapping


def test_supply_chain_mapping_golden_prd_242_semantics(db, admin_user):
    ontology = OntologyProject(
        name="供应链 Golden",
        domain="供应链",
        build_mode="pipeline_mapping",
        created_by=admin_user.id,
    )
    db.add(ontology)
    db.commit()
    db.refresh(ontology)

    curated = {
        "SupplierDatabase": _add_curated(db, "supplier_database"),
        "LogisticsPerformance": _add_curated(db, "logistics_performance"),
        "InventoryTransactions": _add_curated(db, "inventory_transactions"),
        "SupplierOrders": _add_curated(db, "supplier_orders"),
        "SupplyChainStrategy": _add_curated(db, "supply_chain_strategy"),
        "ProcurementPolicy": _add_curated(db, "procurement_policy"),
        "SupplyChainReview": _add_curated(db, "supply_chain_review"),
        "WarehouseManagement": _add_curated(db, "warehouse_management"),
    }
    rows_by_dataset = {
        curated["SupplierDatabase"].id: [
            {"供应商ID": "SUP001", "供应商名称": "天钢原材料有限公司", "评级状态": "approved"},
            {"供应商ID": "SUP002", "供应商名称": "华东包装科技", "评级状态": "pending_review"},
        ],
        curated["LogisticsPerformance"].id: [
            {"记录ID": "LP001", "供应商": "SUP001", "准时率": "98.5", "运输状态": "delivered"},
            {"记录ID": "LP002", "供应商": "SUP001", "准时率": "", "运输状态": "delayed"},
            {"记录ID": "LP003", "供应商": "SUP002", "准时率": "93.1", "运输状态": "delivered"},
        ],
        curated["InventoryTransactions"].id: [
            {"流水号": "IT001", "日期": "2026-03-08", "物料编码": "MAT001", "操作类型": "出库", "所在仓库": "仓库A"},
            {"流水号": "IT002", "日期": "2026-03-09", "物料编码": "MAT002", "操作类型": "入库", "所在仓库": "仓库B"},
        ],
        curated["SupplierOrders"].id: [
            {"order_id": "PO-001", "供应商ID": "SUP001", "items.sku": "MAT001", "状态": "submitted"},
            {"order_id": "PO-002", "供应商ID": "SUP002", "items.sku": "MAT002", "状态": "approved"},
        ],
        curated["SupplyChainStrategy"].id: [
            {"strategy_id": "SC-001", "section": "risk", "rule": "IF 准时率 < 95% THEN create_review_task"},
        ],
        curated["ProcurementPolicy"].id: [
            {"policy_id": "PP-001", "section": "approval", "状态": "active", "thresholds": "95%"},
        ],
        curated["SupplyChainReview"].id: [
            {"review_id": "SR-001", "sections": "季度复盘", "状态": "draft"},
        ],
        curated["WarehouseManagement"].id: [
            {"warehouse_id": "WH-001", "所在仓库": "仓库A", "状态": "active"},
        ],
    }

    pk_by_entity = {
        "SupplierDatabase": "供应商ID",
        "LogisticsPerformance": "记录ID",
        "InventoryTransactions": "流水号",
        "SupplierOrders": "order_id",
        "SupplyChainStrategy": "strategy_id",
        "ProcurementPolicy": "policy_id",
        "SupplyChainReview": "review_id",
        "WarehouseManagement": "warehouse_id",
    }
    for entity_class, ds in curated.items():
        _add_mapping(db, ontology.id, ds.id, entity_class, pk_by_entity[entity_class])

    service = MappingService(db)
    with patch("app.services.v2.dataset_service.DatasetService.preview", side_effect=lambda dataset_id, *_args, **_kwargs: rows_by_dataset[dataset_id]), \
         patch.object(MappingService, "_write_neo4j", side_effect=lambda _self, _entity_class, entities: len(entities), autospec=True), \
         patch.object(MappingService, "_write_neo4j_relations", return_value=None), \
         patch("app.services.v2.vector.chroma_service.ChromaService.upsert_entities", return_value=None):
        result = service.build_all(ontology.id)

    assert result["total_entities"] == sum(len(rows) for rows in rows_by_dataset.values())
    assert result["total_relations"] >= 5
    assert result["total_logic"] >= 1
    assert result["total_actions"] >= 1

    entity_types = {row[0] for row in db.query(Entity.type).filter(Entity.ontology_id == ontology.id).distinct().all()}
    assert set(pk_by_entity) <= entity_types

    rels = db.query(Relation).filter(Relation.ontology_id == ontology.id).all()
    assert any(r.type == "HAS_SUPPLIER_DATABASE" and (r.properties or {}).get("source") == "fk_inference" for r in rels)
    assert db.query(OntologyLinkMapping).filter(
        OntologyLinkMapping.ontology_id == ontology.id,
        OntologyLinkMapping.status == "inferred",
    ).count() >= 1

    logic_types = {row[0] for row in db.query(OntologyLogicRule.logic_type).filter(OntologyLogicRule.ontology_id == ontology.id).distinct().all()}
    assert {"mapping", "validation", "state", "inference", "automation"} <= logic_types
    assert db.query(LogicRule).filter(LogicRule.ontology_id == ontology.id).count() >= len(logic_types)

    action_categories = {row[0] for row in db.query(OntologyActionType.action_category).filter(OntologyActionType.ontology_id == ontology.id).distinct().all()}
    assert {"crud", "state_transition", "link", "review", "repair", "writeback"} <= action_categories
    assert db.query(Action).filter(Action.ontology_id == ontology.id).count() >= len(action_categories)

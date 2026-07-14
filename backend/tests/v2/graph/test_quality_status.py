from unittest.mock import MagicMock, patch

from app.models.entity import Entity
from app.models.relation import Relation
from app.routers.v2 import graph as graph_router


def test_graph_quality_reports_isolated_and_duplicate_nodes(db):
    ontology_id = "ont-graph-quality"
    db.add_all([
        Entity(id="e1", ontology_id=ontology_id, name_cn="供应商A", name_en="Supplier", type="Supplier", properties={}),
        Entity(id="e2", ontology_id=ontology_id, name_cn="供应商A", name_en="Supplier", type="Supplier", properties={}),
        Entity(id="e3", ontology_id=ontology_id, name_cn="订单1", name_en="Order", type="Order", properties={}),
    ])
    db.add(Relation(
        id="r1",
        ontology_id=ontology_id,
        source_entity="e3",
        target_entity="e1",
        type="HAS_SUPPLIER",
        properties={},
    ))
    db.commit()

    with patch.object(graph_router, "SessionLocal", return_value=db):
        result = graph_router.graph_quality(ontology_id)

    assert result["node_count"] == 3
    assert result["edge_count"] == 1
    assert result["isolated_node_count"] == 1
    assert result["duplicate_display_name_count"] == 2
    assert result["object_type_counts"]["Supplier"] == 2
    assert result["relation_type_counts"]["HAS_SUPPLIER"] == 1
    assert result["quality_score"] < 1


def test_integration_status_reports_neo4j_and_chroma():
    fake_neo = MagicMock()
    fake_neo.available = True
    fake_chroma = MagicMock()
    fake_chroma.available = True
    fake_chroma.count.return_value = 42

    with patch.object(graph_router, "get_neo4j", return_value=fake_neo), \
         patch("app.services.v2.vector.chroma_service.ChromaService", return_value=fake_chroma):
        result = graph_router.integration_status("ont-1")

    assert result["neo4j"]["available"] is True
    assert result["chroma"]["available"] is True
    assert result["chroma"]["entity_count"] == 42
    fake_neo.close.assert_called_once()


def test_sqlite_graph_fallback_returns_nodes_and_edges(db):
    ontology_id = "ont-sqlite-graph"
    db.add_all([
        Entity(id="supplier-1", ontology_id=ontology_id, name_cn="供应商A", name_en="Supplier A", type="SupplierDatabase", properties={}),
        Entity(id="order-1", ontology_id=ontology_id, name_cn="PO-1", name_en="PO-1", type="SupplierOrders", properties={}),
    ])
    db.add(Relation(
        id="rel-1",
        ontology_id=ontology_id,
        source_entity="order-1",
        target_entity="supplier-1",
        type="HAS_SUP",
        properties={"source": "test"},
    ))
    db.commit()

    with patch.object(graph_router, "SessionLocal", return_value=db):
        result = graph_router._sqlite_graph_data(ontology_id)

    assert result["neo4j_available"] is False
    assert result["fallback"] == "sqlite"
    assert {node["id"] for node in result["nodes"]} == {"supplier-1", "order-1"}
    assert result["edges"][0]["source"] == "order-1"
    assert result["edges"][0]["target"] == "supplier-1"

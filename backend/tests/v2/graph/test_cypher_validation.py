"""Cypher 只读校验 — 词边界 + ontology_id 隔离"""
from app.services.v2.graph.cypher_builder import validate_readonly_cypher


def test_blocks_write_keywords():
    assert validate_readonly_cypher("CREATE (n:X) RETURN n") is not None
    assert validate_readonly_cypher("MATCH (n) DETACH DELETE n") is not None
    assert validate_readonly_cypher("match (n) set n.x = 1 return n") is not None
    assert validate_readonly_cypher("LOAD CSV FROM 'file:///x' AS row RETURN row") is not None


def test_substring_keywords_not_false_positive():
    """属性名含 SET/DROP 子串不应被误拦"""
    q = "MATCH (n) WHERE n.ontology_id = $ontology_id AND n.asset_id = 'A1' RETURN n.backdrop"
    assert validate_readonly_cypher(q) is None


def test_requires_ontology_id_filter():
    err = validate_readonly_cypher("MATCH (n) RETURN n LIMIT 10")
    assert err is not None and "ontology_id" in err


def test_valid_scoped_query_passes():
    q = "MATCH (n) WHERE n.ontology_id = $ontology_id RETURN n LIMIT 25"
    assert validate_readonly_cypher(q) is None

"""Ontology read tool wrappers (P4A-GATEWAY).

Trusted local read implementations invoked only through the ToolGateway:
SQL instance query/projection and bounded relation traversal, both
release-filtered and SQL-refetched.  Every call returns an audit-able
outcome payload; no writes or Actions execute here.
"""
from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.orm import Session


class OntologyToolError(Exception):
    """The read could not be completed (fail closed)."""


def execute_ontology_read(db: Session, *, descriptor_id: str, parameters: dict,
                          correlation_id: str) -> tuple[str, dict]:
    if descriptor_id == "ontology.read_instances":
        return _read_instances(db, parameters, correlation_id)
    if descriptor_id == "ontology.traverse_relations":
        return _traverse_relations(db, parameters, correlation_id)
    if descriptor_id == "ontology.execute_read_logic":
        return ("read", {"logic": parameters.get("logic_id"), "correlation_id": correlation_id})
    raise OntologyToolError("DESCRIPTOR_UNKNOWN")


def _read_instances(db: Session, parameters: dict, correlation_id: str) -> tuple[str, dict]:
    ontology_id = parameters.get("ontology_id")
    release_id = parameters.get("release_id")
    query = parameters.get("query")
    limit = int(parameters.get("limit", 20))
    if not ontology_id or not release_id:
        raise OntologyToolError("READ_PARAMETERS_REQUIRED")
    rows = db.execute(text(
        "SELECT ei.id AS instance_id, ei.entity_id, ei.revision, ei.row_data "
        "FROM entity_instances ei "
        "WHERE ei.ontology_id = :o AND ei.deleted_at IS NULL AND ei.row_data::text ILIKE :q "
        "AND EXISTS (SELECT 1 FROM ontology_releases rel WHERE rel.id = :rid "
        "AND rel.ontology_id = ei.ontology_id "
        "AND rel.manifest_projection @> jsonb_build_object('entities', "
        "jsonb_build_array(jsonb_build_object('id', ei.entity_id)))) "
        "ORDER BY ei.updated_at DESC LIMIT :lim"
    ), {"o": ontology_id, "rid": release_id, "q": f"%{query}%", "lim": limit}).mappings().all()
    # SQL refetch + revision check on every hit
    results = []
    for hit in rows:
        fresh = db.execute(text(
            "SELECT id, revision FROM entity_instances WHERE id = :id AND deleted_at IS NULL "
            "AND revision = :rev"
        ), {"id": hit["instance_id"], "rev": hit["revision"]}).mappings().one_or_none()
        if not fresh:
            continue
        results.append({
            "instance_id": hit["instance_id"], "entity_id": hit["entity_id"],
            "revision": hit["revision"], "row_data": hit["row_data"],
        })
    return ("read", {"items": results, "correlation_id": correlation_id})


def _traverse_relations(db: Session, parameters: dict, correlation_id: str) -> tuple[str, dict]:
    ontology_id = parameters.get("ontology_id")
    release_id = parameters.get("release_id")
    instance_id = parameters.get("instance_id")
    limit = int(parameters.get("limit", 20))
    if not ontology_id or not release_id or not instance_id:
        raise OntologyToolError("TRAVERSE_PARAMETERS_REQUIRED")
    rows = db.execute(text(
        "SELECT eir.id AS edge_id, eir.source_instance_id, eir.target_instance_id, "
        "eir.relation_definition_id "
        "FROM entity_instance_relations eir "
        "WHERE eir.ontology_id = :o AND eir.deleted_at IS NULL "
        "AND (eir.source_instance_id = :iid OR eir.target_instance_id = :iid) "
        "AND EXISTS (SELECT 1 FROM ontology_releases rel WHERE rel.id = :rid "
        "AND rel.ontology_id = eir.ontology_id "
        "AND rel.manifest_projection @> jsonb_build_object('relations', "
        "jsonb_build_array(jsonb_build_object('id', eir.relation_definition_id)))) "
        "LIMIT :lim"
    ), {"o": ontology_id, "rid": release_id, "iid": instance_id, "lim": limit}).mappings().all()
    return ("read", {"edges": [dict(r) for r in rows], "correlation_id": correlation_id})

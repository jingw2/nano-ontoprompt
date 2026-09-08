"""Authorized Ontology queries for context assembly (P4A-CONTEXT).

Bounded, policy-checked, SQL-refetched reads of authoritative instances and
relations through the release-aware candidate interface.  Never executes
writes or Actions.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


class OntologyQueryError(Exception):
    """The query could not be authorized or completed (fail closed)."""


def query_instances(
    db: Session, *, ontology_id: str, release_id: str, entity_id: str | None = None,
    query: str | None = None, user_id: str | None = None, limit: int = 20,
) -> list[dict]:
    """Instance rows for an entity (optionally filtered), release-checked and
    authorized by read_instances.  Every hit is SQL-refetched by revision."""
    if user_id is not None:
        grant = db.execute(text(
            "SELECT 1 FROM ontology_data_grants WHERE ontology_id = :o AND user_id = :u "
            "AND status = 'active' AND capabilities::text LIKE '%\"read_instances\"%' LIMIT 1"
        ), {"o": ontology_id, "u": user_id}).scalar_one_or_none()
        if not grant:
            return []
    params: dict = {"o": ontology_id, "rid": release_id, "lim": limit}
    filters = [
        "ei.ontology_id = :o",
        "ei.deleted_at IS NULL",
        "EXISTS (SELECT 1 FROM ontology_releases rel WHERE rel.id = :rid "
        "AND rel.ontology_id = ei.ontology_id "
        "AND rel.manifest_projection @> jsonb_build_object('entities', "
        "jsonb_build_array(jsonb_build_object('id', ei.entity_id))))",
    ]
    if entity_id:
        filters.append("ei.entity_id = :eid")
        params["eid"] = entity_id
    if query:
        filters.append("ei.row_data::text ILIKE :q")
        params["q"] = f"%{query}%"
    rows = db.execute(text(
        "SELECT ei.id AS instance_id, ei.entity_id, ei.revision, ei.row_data "
        "FROM entity_instances ei WHERE " + " AND ".join(filters) + " "
        "ORDER BY ei.updated_at DESC LIMIT :lim"
    ), params).mappings().all()
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
    return results


def query_relations(
    db: Session, *, ontology_id: str, release_id: str, instance_id: str,
    user_id: str | None = None, limit: int = 20,
) -> list[dict]:
    """One-hop relations from an instance, authorizing each hop via the
    data grant's traverse_relations capability."""
    if user_id is not None:
        grant = db.execute(text(
            "SELECT 1 FROM ontology_data_grants WHERE ontology_id = :o AND user_id = :u "
            "AND status = 'active' AND capabilities::text LIKE '%\"traverse_relations\"%' LIMIT 1"
        ), {"o": ontology_id, "u": user_id}).scalar_one_or_none()
        if not grant:
            return []
    rows = db.execute(text(
        "SELECT eir.id AS edge_id, eir.source_instance_id, eir.target_instance_id, "
        "eir.relation_definition_id, eir.properties "
        "FROM entity_instance_relations eir "
        "WHERE eir.ontology_id = :o AND eir.deleted_at IS NULL "
        "AND (eir.source_instance_id = :iid OR eir.target_instance_id = :iid) "
        "AND EXISTS (SELECT 1 FROM ontology_releases rel WHERE rel.id = :rid "
        "AND rel.ontology_id = eir.ontology_id "
        "AND rel.manifest_projection @> jsonb_build_object('relations', "
        "jsonb_build_array(jsonb_build_object('id', eir.relation_definition_id)))) "
        "LIMIT :lim"
    ), {"o": ontology_id, "rid": release_id, "iid": instance_id, "lim": limit}).mappings().all()
    return [dict(r) for r in rows]

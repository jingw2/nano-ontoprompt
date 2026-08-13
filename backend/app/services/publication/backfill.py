"""Resumable governed ontology identity inventory (P1B-BACKFILL).

Walks legacy `Entity.properties` in cursor batches, classifies every entry via
the P1A preflight, upserts normalized `EntityPropertyDefinition` rows only for
explicit validated schema metadata, inserts blocking findings, and audits —
without ever mutating the source payload.  Repeated runs converge: conflicts
are no-ops and the source JSON stays byte-identical.
"""
import json
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.services.governance_audit import enqueue_audit
from app.services.publication.preflight import preflight_entity_properties

_FINDING_CLASSIFICATION = {
    "PROPERTY_EXAMPLE_OR_SCALAR": "example_or_scalar",
    "PROPERTY_AMBIGUOUS": "ambiguous",
    "PROPERTY_INVALID_JSON": "invalid",
}


def run_backfill(db: Session, *, batch_size: int = 100, after_id: str | None = None,
                 actor_id: str | None = None, ontology_id: str | None = None) -> dict:
    """Process one cursor batch; returns a report with the next `cursor`."""
    rows = _entity_batch(db, after_id=after_id, limit=batch_size, ontology_id=ontology_id)
    created = updated = inserted = existing = 0
    cursor = None
    domain = None
    for row in rows:
        cursor = row["id"]
        domain = row["security_domain_id"]
        definitions, findings = preflight_entity_properties(
            row["ontology_id"], row["id"], row["name_cn"], row["properties"] or {}
        )
        for definition in definitions:
            if _upsert_definition(db, definition, actor_id=actor_id or row["created_by"]):
                created += 1
            else:
                updated += 1
        for finding in findings:
            if _upsert_finding(db, row["ontology_id"], row["id"], finding):
                inserted += 1
            else:
                existing += 1
    if rows:
        _enqueue_audit(db, security_domain_id=domain, actor_id=actor_id, cursor=cursor)
        db.commit()
    return {
        "entities_scanned": len(rows),
        "definitions_created": created,
        "definitions_existing": updated,
        "findings_inserted": inserted,
        "findings_existing": existing,
        "cursor": cursor if len(rows) == batch_size else None,
    }


def _entity_batch(db: Session, *, after_id: str | None, limit: int, ontology_id: str | None):
    statement = (
        "SELECT e.id, e.ontology_id, e.name_cn, e.properties, o.security_domain_id, o.created_by "
        "FROM entities e JOIN ontology_projects o ON o.id = e.ontology_id WHERE 1 = 1"
    )
    params: dict = {}
    if ontology_id:
        statement += " AND e.ontology_id = :o"
        params["o"] = ontology_id
    if after_id:
        statement += " AND e.id > :after"
        params["after"] = after_id
    statement += " ORDER BY e.id LIMIT :limit"
    params["limit"] = limit
    return db.execute(sa.text(statement), params).mappings().all()


def _upsert_definition(db: Session, definition: dict, *, actor_id: str) -> bool:
    default = definition.get("default_value")
    result = db.execute(
        sa.text(
            "INSERT INTO entity_property_definitions "
            "(id, ontology_id, entity_id, key, normalized_key, value_type, required, default_value, "
            "constraints, sensitivity, ordinal, created_by) "
            "VALUES (:id, :ontology_id, :entity_id, :key, :normalized_key, :value_type, :required, "
            "CAST(:default AS jsonb), CAST(:constraints AS jsonb), :sensitivity, 0, :created_by) "
            "ON CONFLICT (entity_id, normalized_key) DO NOTHING"
        ),
        {
            "id": definition["id"],
            "ontology_id": definition["ontology_id"],
            "entity_id": definition["entity_id"],
            "key": definition["key"],
            "normalized_key": definition["normalized_key"],
            "value_type": definition["value_type"],
            "required": definition["required"],
            "default": None if default is None else json.dumps(default, ensure_ascii=False),
            "constraints": json.dumps(definition["constraints"], ensure_ascii=False, sort_keys=True),
            "sensitivity": definition["sensitivity"],
            "created_by": actor_id,
        },
    )
    return result.rowcount == 1


def _upsert_finding(db: Session, ontology_id: str, entity_id: str, finding: dict) -> bool:
    item_id = finding["path"].rsplit("/", 1)[-1]
    result = db.execute(
        sa.text(
            "INSERT INTO ontology_migration_findings "
            "(id, ontology_id, entity_id, kind, item_id, code, path, message, source_hash, "
            "classification, status, revision) "
            "VALUES (:id, :ontology_id, :entity_id, 'property', :item_id, :code, :path, :message, "
            ":source_hash, :classification, 'open', 1) "
            "ON CONFLICT (ontology_id, kind, item_id, code) DO NOTHING"
        ),
        {
            "id": str(uuid.uuid4()),
            "ontology_id": ontology_id,
            "entity_id": entity_id,
            "item_id": item_id,
            "code": finding["code"],
            "path": finding["path"],
            "message": finding["message"],
            "source_hash": finding["source_hash"],
            "classification": _FINDING_CLASSIFICATION.get(finding["code"]),
        },
    )
    return result.rowcount == 1


def _enqueue_audit(db: Session, *, security_domain_id: str, actor_id: str | None, cursor: str) -> None:
    enqueue_audit(
        db.connection(),
        security_domain_id=security_domain_id,
        correlation_id=f"backfill:{uuid.uuid4().hex[:12]}:{cursor}",
        operation="ontology.identity.backfill",
        decision="allow",
        outcome="succeeded",
        actor_user_id=actor_id,
        retention_class="standard",
    )

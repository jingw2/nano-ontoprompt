"""Typed migration remediation for governed ontology identities.

Property remediation requires a complete explicit contract, preserves the
source payload, creates or updates the normalized definition under CAS
(`base_working_revision` plus the finding's `source_hash`), resolves the
finding, increments the working revision, and audits atomically.  Executable
contract remediation activates with the P1B cutover and never infers fields.
"""
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.services.governance_audit import enqueue_audit
from app.services.publication.preflight import (
    classify_property,
    normalize_property_key,
    stable_property_definition_id,
)


class RemediationConflict(Exception):
    pass


class RemediationNotFound(Exception):
    pass


def _enqueue_audit(db: Session, *, security_domain_id: str, correlation_id: str,
                   operation: str, actor_user_id: str) -> None:
    enqueue_audit(
        db.connection(),
        security_domain_id=security_domain_id,
        correlation_id=correlation_id,
        operation=operation,
        decision="allow",
        outcome="succeeded",
        actor_user_id=actor_user_id,
        retention_class="standard",
    )


def _open_finding(db: Session, ontology_id: str, kind: str, item_id: str, *, for_update: bool):
    lock = " FOR UPDATE" if for_update else ""
    return db.execute(
        sa.text(
            "SELECT id, ontology_id, entity_id, kind, item_id, code, path, message, source_hash, "
            "classification, status, revision, created_at "
            "FROM ontology_migration_findings "
            "WHERE ontology_id = :o AND kind = :k AND item_id = :i AND status = 'open' "
            "ORDER BY created_at LIMIT 1" + lock
        ),
        {"o": ontology_id, "k": kind, "i": item_id},
    ).mappings().one_or_none()


def remediate_property(db: Session, *, ontology_id: str, request, actor_id: str) -> dict:
    finding = _open_finding(db, ontology_id, "property", request.property_key, for_update=True)
    if finding is None:
        raise RemediationNotFound("no open property finding")
    try:
        expected_hash = bytes.fromhex(request.source_hash)
    except ValueError as exc:
        raise RemediationConflict("SOURCE_HASH_INVALID") from exc
    if bytes(finding["source_hash"]) != expected_hash:
        raise RemediationConflict("SOURCE_HASH_MISMATCH")
    updated = db.execute(
        sa.text(
            "UPDATE ontology_projects SET working_revision = working_revision + 1, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = :id AND working_revision = :base"
        ),
        {"id": ontology_id, "base": request.base_working_revision},
    )
    if updated.rowcount == 0:
        raise RemediationConflict("ONTOLOGY_WORKING_REVISION_CONFLICT")
    classification, detail = classify_property(dict(request.explicit_schema_metadata))
    if classification != "explicit_schema":
        raise RemediationConflict(f"INVALID_PROPERTY_SCHEMA: {detail['reason']}")
    entity_id = finding["entity_id"]
    normalized = normalize_property_key(request.property_key)
    definition_id = detail.get("id") or stable_property_definition_id(
        ontology_id, entity_id, request.property_key
    )
    existing = db.execute(
        sa.text(
            "SELECT id FROM entity_property_definitions WHERE entity_id = :e AND normalized_key = :n"
        ),
        {"e": entity_id, "n": normalized},
    ).scalar_one_or_none()
    params = {
        "value_type": detail["value_type"],
        "required": detail["required"],
        "default": None if detail.get("default") is None else _json_text(detail.get("default")),
        "constraints": _json_text(detail["constraints"]),
        "sensitivity": detail["sensitivity"],
    }
    if existing is None:
        db.execute(
            sa.text(
                "INSERT INTO entity_property_definitions "
                "(id, ontology_id, entity_id, key, normalized_key, value_type, required, default_value, "
                "constraints, sensitivity, ordinal, created_by) "
                "VALUES (:id, :ontology_id, :entity_id, :key, :normalized, :value_type, :required, "
                "CAST(:default AS jsonb), CAST(:constraints AS jsonb), :sensitivity, 0, :creator)"
            ),
            {
                "id": definition_id,
                "ontology_id": ontology_id,
                "entity_id": entity_id,
                "key": request.property_key,
                "normalized": normalized,
                "creator": actor_id,
                **params,
            },
        )
    else:
        db.execute(
            sa.text(
                "UPDATE entity_property_definitions SET value_type = :value_type, required = :required, "
                "default_value = CAST(:default AS jsonb), constraints = CAST(:constraints AS jsonb), "
                "sensitivity = :sensitivity, updated_at = CURRENT_TIMESTAMP WHERE id = :id"
            ),
            {"id": existing, **params},
        )
    db.execute(
        sa.text(
            "UPDATE ontology_migration_findings SET status = 'resolved', updated_at = CURRENT_TIMESTAMP "
            "WHERE id = :id"
        ),
        {"id": finding["id"]},
    )
    domain = db.execute(
        sa.text("SELECT security_domain_id FROM ontology_projects WHERE id = :id"),
        {"id": ontology_id},
    ).scalar_one()
    _enqueue_audit(
        db, security_domain_id=domain,
        correlation_id=f"remediation:{ontology_id}:{request.property_key}",
        operation="ontology.remediation.property", actor_user_id=actor_id,
    )
    db.commit()
    working_revision = db.execute(
        sa.text("SELECT working_revision FROM ontology_projects WHERE id = :id"),
        {"id": ontology_id},
    ).scalar_one()
    definition_row = db.execute(
        sa.text(
            "SELECT id, key, normalized_key, value_type, required, sensitivity, constraints::text "
            "FROM entity_property_definitions WHERE id = :id"
        ),
        {"id": definition_id},
    ).mappings().one()
    finding_result = dict(finding)
    finding_result["source_hash"] = bytes(finding["source_hash"]).hex()
    finding_result["status"] = "resolved"
    return {
        "finding": finding_result,
        "definition": dict(definition_row),
        "working_revision": working_revision,
    }


def remediate_executable(db: Session, *, ontology_id: str, request, actor_id: str) -> dict:
    """Executable contract remediation activates with the P1B cutover."""
    raise RemediationConflict(
        "EXECUTABLE_SCHEMA_MIGRATION_REQUIRED: executable contract remediation "
        "activates with the P1B cutover and never infers fields"
    )


def _json_text(value) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True)

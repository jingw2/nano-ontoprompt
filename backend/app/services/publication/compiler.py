"""Publication compiler (P1C-COMPILER).

Locks the Ontology project, validates stable Entity/property/Relation IDs and
relation endpoints plus collision-free labels, builds the canonical manifest,
computes SHA-256 bytes, rejects only when the locked latest release has the
same hash (NO_SCHEMA_CHANGE), otherwise inserts the immutable N+1 release,
updates the pointer/status/dirty state, and writes the audit outbox in one
transaction.  Failure changes nothing and emits stable `{code, path, message}`
findings.
"""
from datetime import datetime, timezone
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.services.governance_audit import enqueue_audit
from app.services.publication.canonical import CanonicalizationError, canonical_manifest

SECURITY_DOMAIN_JSON = '{"security_domain_id": "00000000-0000-0000-0000-000000000001"}'


class CompilerFinding(Exception):
    def __init__(self, code: str, path: str, message: str):
        super().__init__(message)
        self.code = code
        self.path = path


class NoSchemaChange(Exception):
    """The locked latest release already has the identical manifest."""


class PublicationBlocked(Exception):
    pass


def preflight_ontology(db: Session, ontology_id: str) -> list[dict]:
    """Validate stable identities, relation endpoints, and label collisions."""
    findings: list[dict] = []
    entities = db.execute(
        sa.text(
            "SELECT id, name_cn, name_en, type, description FROM entities "
            "WHERE ontology_id = :o ORDER BY id"
        ),
        {"o": ontology_id},
    ).mappings().all()
    entity_ids = set()
    for entity in entities:
        if not entity["id"] or not str(entity["id"]).strip():
            findings.append({"code": "INVALID_ENTITY_ID", "path": f"entities/{entity['id']}",
                             "message": "entity id is empty"})
        entity_ids.add(entity["id"])
        duplicates = db.execute(
            sa.text(
                "SELECT count(*) FROM entity_property_definitions "
                "WHERE entity_id = :e GROUP BY normalized_key HAVING count(*) > 1"
            ),
            {"e": entity["id"]},
        ).scalar_one_or_none()
        if duplicates:
            findings.append({"code": "DUPLICATE_NORMALIZED_PROPERTY", "path": f"entities/{entity['id']}",
                             "message": "duplicate normalized property keys"})
    relations = db.execute(
        sa.text(
            "SELECT id, source_entity, target_entity, type FROM relations "
            "WHERE ontology_id = :o ORDER BY id"
        ),
        {"o": ontology_id},
    ).mappings().all()
    for relation in relations:
        if relation["source_entity"] not in entity_ids or relation["target_entity"] not in entity_ids:
            findings.append({"code": "UNRESOLVED_RELATION_ENDPOINT",
                             "path": f"relations/{relation['id']}",
                             "message": "relation endpoint does not resolve to an entity"})
    names: dict[str, str] = {}
    for entity in entities:
        label = entity["name_cn"] or entity["name_en"] or entity["id"]
        if label in names:
            findings.append({"code": "LABEL_COLLISION",
                             "path": f"entities/{entity['id']}",
                             "message": f"display label collides with {names[label]}"})
        names[label] = entity["id"]
    return findings


def _manifest_payload(db: Session, ontology_id: str, version_no: int) -> dict:
    ontology = db.execute(
        sa.text(
            "SELECT id, name, security_domain_id, description, build_mode FROM ontology_projects "
            "WHERE id = :o"
        ),
        {"o": ontology_id},
    ).mappings().one()
    entities = []
    rows = db.execute(
        sa.text(
            "SELECT id, name_cn, name_en, type, description FROM entities "
            "WHERE ontology_id = :o ORDER BY id"
        ),
        {"o": ontology_id},
    ).mappings().all()
    for entity in rows:
        properties = db.execute(
            sa.text(
                "SELECT id, key, value_type, required, default_value, constraints, sensitivity "
                "FROM entity_property_definitions WHERE entity_id = :e ORDER BY id"
            ),
            {"e": entity["id"]},
        ).mappings().all()
        entities.append({
            "id": entity["id"],
            "name": entity["name_cn"] or entity["name_en"] or entity["id"],
            "type": entity["type"] or "object",
            "description": entity["description"],
            "property_definitions": [
                {
                    "id": prop["id"],
                    "name": prop["key"],
                    "type": prop["value_type"],
                    "required": prop["required"],
                    "default": prop["default_value"],
                    "constraints": prop["constraints"] or {},
                    "sensitivity": prop["sensitivity"],
                }
                for prop in properties
            ],
        })
    relations = []
    relation_rows = db.execute(
        sa.text(
            "SELECT id, source_entity, target_entity, type, properties FROM relations "
            "WHERE ontology_id = :o ORDER BY id"
        ),
        {"o": ontology_id},
    ).mappings().all()
    for relation in relation_rows:
        relations.append({
            "id": relation["id"],
            "name": relation["type"] or relation["id"],
            "source_entity_id": relation["source_entity"],
            "target_entity_id": relation["target_entity"],
            "cardinality": "many_to_one",
            "direction": "directed",
            "properties": [],
        })
    return {
        "manifest_version": "ontology-manifest-v1",
        "compiler_version": "ontology-compiler-v1",
        "policy_compiler_version": "restricted-policy-dsl-v1",
        "aggregate_tool_schema_hash": "0" * 64,
        "ontology": {
            "id": ontology["id"],
            "name": ontology["name"],
            "security_domain_id": ontology["security_domain_id"],
            "description": ontology["description"],
            "build_mode": ontology["build_mode"] or "simple_llm",
        },
        "release": {"version_no": version_no, "version": f"v{version_no}"},
        "entities": entities,
        "relations": relations,
        "logic_rules": [],
        "state_machines": [],
        "actions": [],
        "tool_descriptors": [],
    }


def compile_ontology_release(db: Session, *, ontology_id: str, actor_id: str,
                             changelog: str | None = None) -> dict:
    """Locked preflight/compile/insert/pointer/audit transaction."""
    project = db.execute(
        sa.text(
            "SELECT id, security_domain_id, latest_published_release_id, status, working_revision "
            "FROM ontology_projects WHERE id = :id FOR UPDATE"
        ),
        {"id": ontology_id},
    ).mappings().one_or_none()
    if project is None:
        raise PublicationBlocked("ONTOLOGY_NOT_FOUND")
    if project["status"] not in ("created", "published"):
        raise PublicationBlocked("INVALID_LIFECYCLE_TRANSITION")
    findings = preflight_ontology(db, ontology_id)
    if findings:
        raise CompilerFinding("PUBLICATION_BLOCKED", f"ontologies/{ontology_id}",
                              "; ".join(f"{f['code']} {f['path']}" for f in findings))
    latest = db.execute(
        sa.text(
            "SELECT id, version_no, schema_hash FROM ontology_releases "
            "WHERE ontology_id = :o ORDER BY version_no DESC LIMIT 1"
        ),
        {"o": ontology_id},
    ).mappings().one_or_none()
    # no-change compares content at the locked latest version number
    if latest is not None:
        try:
            check = canonical_manifest(_manifest_payload(db, ontology_id, latest["version_no"]))
        except CanonicalizationError as exc:
            raise CompilerFinding("MANIFEST_INVALID", f"ontologies/{ontology_id}", str(exc)) from exc
        if bytes(latest["schema_hash"]) == check.schema_hash:
            raise NoSchemaChange("NO_SCHEMA_CHANGE")
    next_no = (latest["version_no"] + 1) if latest is not None else 1
    payload = _manifest_payload(db, ontology_id, next_no)
    try:
        canonical = canonical_manifest(payload)
    except CanonicalizationError as exc:
        raise CompilerFinding("MANIFEST_INVALID", f"ontologies/{ontology_id}", str(exc)) from exc
    release_id = str(uuid.uuid4())
    db.execute(
        sa.text(
            "INSERT INTO ontology_releases (id, ontology_id, version_no, version, manifest_bytes, "
            "manifest_projection, schema_hash, created_by) "
            "VALUES (:id, :o, :no, :version, :bytes, CAST(:projection AS jsonb), :hash, :creator)"
        ),
        {
            "id": release_id,
            "o": ontology_id,
            "no": next_no,
            "version": f"v{next_no}",
            "bytes": canonical.bytes,
            "projection": canonical.projection,
            "hash": canonical.schema_hash,
            "creator": actor_id,
        },
    )
    db.execute(
        sa.text(
            "UPDATE ontology_projects SET latest_published_release_id = :release, status = 'published', "
            "is_dirty = false, updated_at = CURRENT_TIMESTAMP WHERE id = :o"
        ),
        {"release": release_id, "o": ontology_id},
    )
    enqueue_audit(
        db.connection(),
        security_domain_id=project["security_domain_id"],
        correlation_id=f"release:{ontology_id}:{next_no}",
        operation="ontology.publish",
        decision="allow",
        outcome="succeeded",
        actor_user_id=actor_id,
        release_id=release_id,
        input_payload={"changelog": changelog} if changelog else None,
        retention_class="standard",
    )
    db.commit()
    return {
        "release_id": release_id,
        "version_no": next_no,
        "version": f"v{next_no}",
        "schema_hash": canonical.schema_hash.hex(),
        "manifest_projection": canonical.projection,
    }

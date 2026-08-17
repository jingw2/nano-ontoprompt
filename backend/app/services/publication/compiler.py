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
import hashlib
from decimal import Decimal
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


def _tool_descriptors(db: Session, ontology_id: str) -> list[dict]:
    """Published executable tool descriptors for an Ontology (P2B-TOOLS):
    one deterministic built-in query descriptor plus one descriptor per enabled
    Logic rule and Action (executable read Logic / instance Actions).  The
    descriptors ride the immutable release manifest so the Agent runtime and
    the tool-exposure API read the same pinned snapshot."""
    descriptors: list[dict] = []
    descriptors.append({
        "descriptor_id": f"query:{ontology_id}",
        "version": 1,
        "source_kind": "builtin",
        "source_id": "query",
        "input_schema": {
            "query": {"type": "string", "description": "关键词，匹配实例数据（可选）"},
            "sort_by": {"type": "string",
                        "description": "按此字段排序找最大/最小值时使用（实例数据中的字段名，可选）"},
            "sort_order": {"type": "string", "enum": ["asc", "desc"],
                           "description": "排序方向，配合 sort_by 使用（可选）"},
        },
        "output_schema": {"results": {"type": "array"}},
        "capability": "read_instances",
        "timeout_ms": 10_000,
        "result_limit": 10,
        "descriptor_hash": hashlib.sha256(f"query:{ontology_id}".encode()).hexdigest(),
    })
    logic = db.execute(
        sa.text(
            "SELECT id, name, description, target_entity_type, expression, enabled, version "
            "FROM v2_ontology_logic_rules WHERE ontology_id = :o AND enabled = true ORDER BY id"
        ),
        {"o": ontology_id},
    ).mappings().all()
    for rule in logic:
        rule_id = rule["id"]
        descriptors.append({
            "descriptor_id": f"logic:{rule_id}",
            "version": rule["version"],
            "source_kind": "logic",
            "source_id": rule_id,
            "input_schema": {
                "entity_type": {"type": "string"},
                "parameters": {"type": "object"},
            },
            "output_schema": {"result": {"type": "object"}},
            "capability": "execute_read_logic",
            "timeout_ms": 10_000,
            "result_limit": 1,
            "descriptor_hash": hashlib.sha256(f"logic:{rule_id}".encode()).hexdigest(),
        })
    actions = db.execute(
        sa.text(
            "SELECT id, name, description, target_entity_type, parameters, enabled, version "
            "FROM v2_ontology_action_types WHERE ontology_id = :o AND enabled = true ORDER BY id"
        ),
        {"o": ontology_id},
    ).mappings().all()
    for action in actions:
        action_id = action["id"]
        descriptors.append({
            "descriptor_id": f"action:{action_id}",
            "version": action["version"],
            "source_kind": "action",
            "source_id": action_id,
            "input_schema": {"parameters": {"type": "object"}},
            "output_schema": {"result": {"type": "object"}},
            "capability": "execute_instance_action",
            "timeout_ms": 30_000,
            "result_limit": 1,
            "descriptor_hash": hashlib.sha256(f"action:{action_id}".encode()).hexdigest(),
        })
    return descriptors


def _manifest_json(value):
    """Normalize DB-decoded JSON for the closed manifest domain: JSONB numbers
    arrive as Python floats (22.0), but the manifest domain accepts int and
    Decimal only — integral floats collapse to int, others become Decimal
    (exact, string-rendered)."""
    if isinstance(value, float):
        return int(value) if value.is_integer() else Decimal(str(value))
    if isinstance(value, dict):
        return {key: _manifest_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_manifest_json(item) for item in value]
    return value


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
    return _manifest_json({
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
        "logic_rules": [
            {
                "id": rule["id"],
                "fully_qualified_label": rule["name"],
                "version": rule["version"],
                "input_schema": {"entity_type": {"type": "string"},
                                 "parameters": {"type": "object"}},
                "output_schema": {"result": {"type": "object"}},
                "expression": rule["expression"] or {},
                "effect_classification": rule["logic_type"] or "validation",
                "enabled": bool(rule["enabled"]),
            }
            for rule in db.execute(
                sa.text(
                    "SELECT id, name, logic_type, expression, enabled, version "
                    "FROM v2_ontology_logic_rules WHERE ontology_id = :o ORDER BY id"
                ),
                {"o": ontology_id},
            ).mappings().all()
        ],
        "state_machines": [],
        "actions": [
            {
                "id": action["id"],
                "fully_qualified_label": action["name"],
                "version": action["version"],
                "parameter_schema": {"parameters": {"type": "object"}},
                "result_schema": {"result": {"type": "object"}},
                "declared_instance_effects": action["effects"] or [],
                "risk": (action["side_effects"] or {}).get("risk", "low")
                if isinstance(action["side_effects"], dict) else "low",
                "approval_policy": action["permission_rules"] or {},
                "enabled": bool(action["enabled"]),
            }
            for action in db.execute(
                sa.text(
                    "SELECT id, name, effects, side_effects, permission_rules, enabled, version "
                    "FROM v2_ontology_action_types WHERE ontology_id = :o ORDER BY id"
                ),
                {"o": ontology_id},
            ).mappings().all()
        ],
        "tool_descriptors": _tool_descriptors(db, ontology_id),
    })


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

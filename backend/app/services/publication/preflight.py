"""Governed ontology identity preflight.

Classifies every legacy `Entity.properties` entry as
`explicit_schema|example_or_scalar|ambiguous|invalid` and reports blocking
`{code,path,message,source_hash}` findings.  The source payload is never
mutated and an example value or scalar is never interpreted as a schema
type/default.  `normalize_property_key` is the `property-key-c14n-v1` explicit
stored-column producer (Unicode NFKC, trim/collapse whitespace, case-fold).

The migration helper `upgrade_identity_foundation()`/`downgrade_identity_foundation()`
creates the `entity_property_definitions` and append-only
`ontology_migration_findings` tables and is consumed exactly once by revision
0003 (P1A-INTEGRATE).
"""
import hashlib
import re
import unicodedata
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.services.publication.canonical import CanonicalizationError, canonical_json

UUID_CHECK = (
    "VALUE ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    "[0-9a-f]{4}-[0-9a-f]{12}$'"
)
VALUE_TYPES = ("string", "number", "integer", "boolean", "date", "datetime", "object", "array")
SENSITIVITIES = ("public", "internal", "sensitive", "restricted")
_SCHEMA_KEYS = frozenset({"type", "required", "default", "constraints", "sensitivity", "id"})
_VALUE_KEYS = frozenset({"example", "value"})
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def normalize_property_key(key: str) -> str:
    """property-key-c14n-v1: Unicode NFKC, trim/collapse whitespace, case-fold."""
    return " ".join(unicodedata.normalize("NFKC", key).split()).casefold()


def is_canonical_uuid(value) -> bool:
    return isinstance(value, str) and bool(_UUID_RE.match(value))


def stable_property_definition_id(ontology_id: str, entity_id: str, property_key: str) -> str:
    """Lowercase-hyphenated UUIDv5 over (ontology_id, entity_id, property key bytes)."""
    return str(uuid.uuid5(uuid.NAMESPACE_OID, f"{ontology_id}\u0000{entity_id}\u0000{property_key}"))


def _payload_hash(payload) -> bytes:
    try:
        return hashlib.sha256(canonical_json(payload)).digest()
    except CanonicalizationError:
        return hashlib.sha256(str(payload).encode("utf-8")).digest()


def classify_property(payload):
    """Return (classification, detail) for one legacy property entry.

    `explicit_schema` requires a valid declared value type and validated
    optional metadata; anything schema-ish without a valid type is `ambiguous`;
    plain scalar/example values are `example_or_scalar`; non-object JSON is
    `invalid`.
    """
    if isinstance(payload, dict):
        if "type" in payload:
            declared_type = payload["type"]
            if declared_type not in VALUE_TYPES:
                return ("ambiguous", {"reason": f"unknown value_type '{declared_type}'"})
            errors = []
            if "required" in payload and not isinstance(payload["required"], bool):
                errors.append("required must be boolean")
            if "constraints" in payload and not isinstance(payload["constraints"], dict):
                errors.append("constraints must be an object")
            if "sensitivity" in payload and payload["sensitivity"] not in SENSITIVITIES:
                errors.append("unknown sensitivity")
            if "id" in payload and not is_canonical_uuid(payload["id"]):
                errors.append("non-canonical id")
            if errors:
                return ("ambiguous", {"reason": "; ".join(errors)})
            return ("explicit_schema", {
                "id": payload.get("id"),
                "value_type": declared_type,
                "required": payload.get("required", False),
                "default": payload.get("default"),
                "constraints": payload.get("constraints", {}),
                "sensitivity": payload.get("sensitivity", "internal"),
            })
        if _SCHEMA_KEYS - {"type"} & set(payload.keys()):
            return ("ambiguous", {"reason": "schema metadata without a valid type"})
        if payload and set(payload.keys()) <= _VALUE_KEYS:
            return ("example_or_scalar", {"reason": "example value only"})
        if not payload:
            return ("example_or_scalar", {"reason": "empty object value"})
        return ("example_or_scalar", {"reason": "plain object value"})
    if isinstance(payload, (str, int, float, bool)):
        return ("example_or_scalar", {"reason": "scalar value, never a schema"})
    return ("invalid", {"reason": "non-object property JSON"})


def preflight_entity_properties(ontology_id: str, entity_id: str, entity_name: str, properties):
    """Inventory one entity's legacy properties.

    Returns `(definitions, findings)`: definitions carry only entries with an
    explicit validated schema contract (explicit canonical UUID retained, a
    stable UUIDv5 written when the ID is missing); findings are the blocking
    `{code,path,message,source_hash}` records.
    """
    definitions = []
    findings = []
    if not isinstance(properties, dict):
        return definitions, [{
            "code": "PROPERTY_INVALID_JSON",
            "path": f"entities/{entity_id}/properties",
            "message": f"properties of entity '{entity_name}' is not a JSON object",
            "source_hash": _payload_hash(properties),
        }]
    seen_normalized = {}
    for key, payload in properties.items():
        normalized = normalize_property_key(key)
        path = f"entities/{entity_id}/properties/{key}"
        source_hash = _payload_hash(payload)
        if normalized in seen_normalized:
            findings.append({
                "code": "PROPERTY_DUPLICATE_NORMALIZED_KEY",
                "path": path,
                "message": (
                    f"normalized property key '{normalized}' of entity '{entity_name}' "
                    "duplicates another property key"
                ),
                "source_hash": source_hash,
            })
            continue
        seen_normalized[normalized] = key
        classification, detail = classify_property(payload)
        if classification == "explicit_schema":
            definitions.append({
                "id": detail.get("id") or stable_property_definition_id(ontology_id, entity_id, key),
                "ontology_id": ontology_id,
                "entity_id": entity_id,
                "key": key,
                "normalized_key": normalized,
                "value_type": detail["value_type"],
                "required": detail["required"],
                "default_value": detail.get("default"),
                "constraints": detail["constraints"],
                "sensitivity": detail["sensitivity"],
            })
        else:
            code = {
                "example_or_scalar": "PROPERTY_EXAMPLE_OR_SCALAR",
                "ambiguous": "PROPERTY_AMBIGUOUS",
                "invalid": "PROPERTY_INVALID_JSON",
            }[classification]
            findings.append({
                "code": code,
                "path": path,
                "message": detail["reason"],
                "source_hash": source_hash,
            })
    return definitions, findings


def upgrade_identity_foundation() -> None:
    op.create_table(
        "entity_property_definitions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("ontology_id", sa.String(36), nullable=False),
        sa.Column("entity_id", sa.String(36), nullable=False),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("normalized_key", sa.String(200), nullable=False),
        sa.Column("display_label", sa.String(200), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("default_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("value_type", sa.String(40), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("constraints", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("sensitivity", sa.String(20), nullable=False, server_default="internal"),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deprecated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deprecated_by", sa.String(36), nullable=True),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_entity_property_definitions"),
        sa.CheckConstraint(UUID_CHECK.replace("VALUE", "id"), name="ck_entity_property_definitions_id_uuid"),
        sa.CheckConstraint(
            f"value_type IN ({', '.join(repr(value) for value in VALUE_TYPES)})",
            name="ck_entity_property_definitions_value_type",
        ),
        sa.CheckConstraint(
            f"sensitivity IN ({', '.join(repr(value) for value in SENSITIVITIES)})",
            name="ck_entity_property_definitions_sensitivity",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(constraints) = 'object'",
            name="ck_entity_property_definitions_constraints_shape",
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_entity_property_definitions_ordinal"),
        sa.UniqueConstraint("entity_id", "normalized_key", name="uq_entity_property_definitions_entity_normalized_key"),
        sa.ForeignKeyConstraint(["ontology_id"], ["ontology_projects.id"], ondelete="RESTRICT", name="fk_entity_property_definitions_ontology"),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"], ondelete="RESTRICT", name="fk_entity_property_definitions_entity"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT", name="fk_entity_property_definitions_creator"),
    )
    op.create_table(
        "ontology_migration_findings",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("ontology_id", sa.String(36), nullable=False),
        sa.Column("entity_id", sa.String(36), nullable=True),
        sa.Column("kind", sa.String(60), nullable=False),
        sa.Column("item_id", sa.String(200), nullable=False),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("path", sa.String(500), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.LargeBinary(), nullable=False),
        sa.Column("classification", sa.String(30), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_ontology_migration_findings"),
        sa.CheckConstraint(UUID_CHECK.replace("VALUE", "id"), name="ck_ontology_migration_findings_id_uuid"),
        sa.CheckConstraint("octet_length(source_hash) = 32", name="ck_ontology_migration_findings_source_hash"),
        sa.CheckConstraint("status IN ('open', 'resolved')", name="ck_ontology_migration_findings_status"),
        sa.UniqueConstraint("ontology_id", "kind", "item_id", "code", name="uq_ontology_migration_findings_scope"),
        sa.ForeignKeyConstraint(["ontology_id"], ["ontology_projects.id"], ondelete="RESTRICT", name="fk_ontology_migration_findings_ontology"),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"], ondelete="RESTRICT", name="fk_ontology_migration_findings_entity"),
    )
    op.create_index("ix_ontology_migration_findings_status", "ontology_migration_findings", ["ontology_id", "status"])


def downgrade_identity_foundation() -> None:
    op.drop_index("ix_ontology_migration_findings_status", table_name="ontology_migration_findings")
    op.drop_table("ontology_migration_findings")
    op.drop_table("entity_property_definitions")

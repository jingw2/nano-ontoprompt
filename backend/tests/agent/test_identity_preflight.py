"""P1A-IDENTITY: governed ontology identity inventory.

Classifies legacy `Entity.properties` JSON entries as
`explicit_schema|example_or_scalar|ambiguous|invalid`, produces the
`property-key-c14n-v1` normalized key, generates stable lowercase-hyphenated
UUID strings, and reports blocking `{code,path,message,source_hash}` findings
without ever mutating the source payload.  The migration helper creates the
`entity_property_definitions` and append-only `ontology_migration_findings`
tables consumed by revision 0003.

PostgreSQL-marked tests use TEST_DATABASE_URL; SQLite never substitutes.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError


BACKEND_DIR = Path(__file__).resolve().parents[2]
MODEL = BACKEND_DIR / "app" / "models" / "entity_property_definition.py"
PREFLIGHT = BACKEND_DIR / "app" / "services" / "publication" / "preflight.py"
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"


def test_p1a_identity_red_contract():
    missing = [path for path in (MODEL, PREFLIGHT) if not path.exists()]
    if missing:
        pytest.fail(
            "RED_P1A_IDENTITY: identity preflight foundation missing: "
            + ", ".join(str(path.relative_to(BACKEND_DIR)) for path in missing)
        )


def _scoped_url(schema):
    from urllib.parse import quote

    return f"{TEST_DATABASE_URL}?options={quote(f'-csearch_path={schema}', safe='-=')}"


@pytest.fixture
def identity_schema():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "p1a_identity_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    yield schema
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def _identity_engine(schema):
    return create_engine(_scoped_url(schema))


def _run_helper(engine, helper_name):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from app.services.publication import preflight

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        preflight.op = Operations(context)
        getattr(preflight, helper_name)()


# ── classifier and key normalization (DB-free) ───────────────────────────────

def test_normalize_property_key_is_property_key_c14n_v1():
    from app.services.publication.preflight import normalize_property_key

    assert normalize_property_key("  Supplier  Name ") == "supplier name"
    assert normalize_property_key("Supplier\tName\n") == "supplier name"
    assert normalize_property_key("ＳＵＰＰＬＩＥＲ") == "supplier"  # NFKC full-width
    assert normalize_property_key("Straße") == "strasse"  # NFKC + casefold
    assert normalize_property_key("  ") == ""
    assert normalize_property_key("供应商 名称") == "供应商 名称"
    assert normalize_property_key("Supplier") == normalize_property_key("supplier")


def test_classifier_explicit_schema_requires_validated_type_metadata():
    from app.services.publication.preflight import classify_property

    classification, detail = classify_property(
        {"id": str(uuid.uuid4()), "type": "string", "required": True,
         "default": None, "constraints": {"maxLength": 50}, "sensitivity": "internal"}
    )
    assert classification == "explicit_schema"
    assert detail["value_type"] == "string"

    classification, _ = classify_property({"type": "number"})
    assert classification == "explicit_schema"

    for payload in (
        {"type": "weird"},
        {"type": "string", "required": "yes"},
        {"type": "string", "constraints": ["maxLength"]},
        {"type": "string", "sensitivity": "secret"},
        {"type": "string", "id": "not-a-uuid"},
    ):
        assert classify_property(payload)[0] == "ambiguous", payload


def test_classifier_never_reads_example_or_scalar_as_schema():
    from app.services.publication.preflight import classify_property

    for payload in (
        "foo",
        3,
        1.5,
        True,
        {"example": "foo"},
        {"value": 3},
        {"default": 3},
        {"required": True},
        {},
    ):
        assert classify_property(payload)[0] != "explicit_schema", payload

    assert classify_property("foo")[0] == "example_or_scalar"
    assert classify_property(3)[0] == "example_or_scalar"
    assert classify_property({"example": "foo"})[0] == "example_or_scalar"
    assert classify_property({"value": 3})[0] == "example_or_scalar"
    assert classify_property({"default": 3})[0] == "ambiguous"
    assert classify_property({"required": True})[0] == "ambiguous"
    assert classify_property([])[0] == "invalid"
    assert classify_property(None)[0] == "invalid"


def test_stable_definition_id_and_canonical_uuid_helpers():
    from app.services.publication.preflight import is_canonical_uuid, stable_property_definition_id

    assert is_canonical_uuid(str(uuid.uuid4())) is True
    assert is_canonical_uuid("not-a-uuid") is False
    assert is_canonical_uuid(str(uuid.uuid4()).upper()) is False
    first = stable_property_definition_id("ontology-1", "entity-1", "supplier code")
    second = stable_property_definition_id("ontology-1", "entity-1", "supplier code")
    assert first == second and is_canonical_uuid(first)
    assert first != stable_property_definition_id("ontology-1", "entity-1", "other")
    assert first != stable_property_definition_id("ontology-1", "entity-2", "supplier code")


def test_preflight_inventory_produces_definitions_and_blocking_findings():
    from app.services.publication.preflight import (
        normalize_property_key, preflight_entity_properties,
    )

    property_id = str(uuid.uuid4())
    properties = {
        "Supplier Code": {"id": property_id, "type": "string", "required": True, "sensitivity": "internal"},
        "supplier code": {"example": "duplicate normalized key"},
        "Amount": {"type": "number"},
        "Description": "free text",
        "Flag": {"required": True},
        "Bad": ["not", "an", "object"],
    }
    definitions, findings = preflight_entity_properties(
        "ontology-1", "entity-1", "供应商", properties
    )
    assert {d["key"] for d in definitions} == {"Supplier Code", "Amount"}
    by_key = {d["key"]: d for d in definitions}
    assert by_key["Supplier Code"]["id"] == property_id  # retained explicit canonical UUID
    assert by_key["Supplier Code"]["normalized_key"] == "supplier code"
    assert by_key["Amount"]["normalized_key"] == "amount"
    assert by_key["Amount"]["id"] is not None  # UUIDv5 backfill candidate

    codes = {f["code"] for f in findings}
    assert codes == {
        "PROPERTY_EXAMPLE_OR_SCALAR",
        "PROPERTY_AMBIGUOUS",
        "PROPERTY_INVALID_JSON",
        "PROPERTY_DUPLICATE_NORMALIZED_KEY",
    }
    duplicate = next(f for f in findings if f["code"] == "PROPERTY_DUPLICATE_NORMALIZED_KEY")
    assert duplicate["path"] == "entities/entity-1/properties/supplier code"
    assert len(duplicate["source_hash"]) == 32
    # the duplicate normalized key never yields a second definition
    assert len([d for d in definitions if d["normalized_key"] == "supplier code"]) == 1
    # explicit schema entries never get a finding
    assert all(f["path"] != "entities/entity-1/properties/Supplier Code" for f in findings)


class _FakeOp:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
        return record


def test_identity_migration_helper_upgrade_and_downgrade_order(monkeypatch):
    from app.services.publication import preflight

    fake = _FakeOp()
    monkeypatch.setattr(preflight, "op", fake)
    preflight.upgrade_identity_foundation()
    names = [name for name, _, _ in fake.calls]
    creates = [i for i, name in enumerate(names) if name == "create_table"]
    assert len(creates) == 2
    tables = {fake.calls[i][1][0] for i in creates}
    assert tables == {"entity_property_definitions", "ontology_migration_findings"}

    fake.calls.clear()
    preflight.downgrade_identity_foundation()
    names = [name for name, _, _ in fake.calls]
    drop_tables = [name for name in names if name == "drop_table"]
    assert drop_tables == ["drop_table", "drop_table"]


# ── ORM storage contract (subprocess keeps the shared metadata SQLite-clean) ─

def test_zzz_identity_orm_exact_storage_and_constraint_contract():
    script = """
import json
from app.models.entity_property_definition import (
    EntityPropertyDefinition, OntologyMigrationFinding,
)
print(json.dumps({
    'columns': list(EntityPropertyDefinition.__table__.c.keys()),
    'constraints': [c.name for c in EntityPropertyDefinition.__table__.constraints],
    'finding_columns': list(OntologyMigrationFinding.__table__.c.keys()),
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=BACKEND_DIR, capture_output=True, text=True, check=True
    )
    metadata = json.loads(result.stdout)
    assert set(metadata["columns"]) == {
        "id", "ontology_id", "entity_id", "key", "normalized_key", "display_label",
        "description", "default_value", "value_type", "required", "constraints",
        "sensitivity", "ordinal", "deprecated_at", "deprecated_by",
        "created_by", "created_at", "updated_at",
    }
    assert set(metadata["constraints"]) >= {
        "ck_entity_property_definitions_id_uuid",
        "ck_entity_property_definitions_value_type",
        "ck_entity_property_definitions_sensitivity",
        "ck_entity_property_definitions_constraints_shape",
        "ck_entity_property_definitions_ordinal",
        "uq_entity_property_definitions_entity_normalized_key",
    }
    assert set(metadata["finding_columns"]) >= {
        "id", "ontology_id", "entity_id", "kind", "item_id", "code", "path",
        "message", "source_hash", "classification", "status", "created_at", "updated_at",
    }


# ── PostgreSQL identity table fixtures ───────────────────────────────────────

def test_zzzz_postgresql_identity_tables_unique_key_and_downgrade(identity_schema):
    engine = _identity_engine(identity_schema)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id varchar PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE ontology_projects (id varchar PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE entities (id varchar PRIMARY KEY, ontology_id varchar NOT NULL)"))
    _run_helper(engine, "upgrade_identity_foundation")

    with engine.connect() as connection:
        inspector = inspect(connection)
        assert {"entity_property_definitions", "ontology_migration_findings"} <= set(inspector.get_table_names())
        definition_fks = {fk["name"]: fk for fk in inspector.get_foreign_keys("entity_property_definitions")}
        assert definition_fks["fk_entity_property_definitions_ontology"]["options"]["ondelete"] == "RESTRICT"
        assert definition_fks["fk_entity_property_definitions_entity"]["options"]["ondelete"] == "RESTRICT"
        assert definition_fks["fk_entity_property_definitions_creator"]["options"]["ondelete"] == "RESTRICT"
        finding_fks = {fk["name"]: fk for fk in inspector.get_foreign_keys("ontology_migration_findings")}
        assert finding_fks["fk_ontology_migration_findings_ontology"]["options"]["ondelete"] == "RESTRICT"

    definition_id = str(uuid.uuid4())
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO users (id) VALUES ('creator')"
        ))
        connection.execute(text(
            "INSERT INTO ontology_projects (id) VALUES ('ontology-1')"
        ))
        connection.execute(text(
            "INSERT INTO entities (id, ontology_id) VALUES ('entity-1', 'ontology-1')"
        ))
        connection.execute(text(
            "INSERT INTO entity_property_definitions "
            "(id, ontology_id, entity_id, key, normalized_key, value_type, required, constraints, sensitivity, ordinal, created_by) "
            "VALUES (:id, 'ontology-1', 'entity-1', 'Supplier Code', 'supplier code', 'string', true, '{}'::jsonb, 'internal', 0, 'creator')"
        ), {"id": definition_id})
        connection.execute(text(
            "INSERT INTO ontology_migration_findings "
            "(id, ontology_id, entity_id, kind, item_id, code, path, message, source_hash, classification, status) "
            "VALUES (:id, 'ontology-1', 'entity-1', 'property', 'Amount', 'PROPERTY_AMBIGUOUS', "
            "'entities/entity-1/properties/Amount', 'schema metadata without a valid type', :hash, 'ambiguous', 'open')"
        ), {"id": str(uuid.uuid4()), "hash": b"a" * 32})
        savepoint = connection.begin_nested()
        with pytest.raises(IntegrityError):
            connection.execute(text(
                "INSERT INTO entity_property_definitions "
                "(id, ontology_id, entity_id, key, normalized_key, value_type, required, constraints, sensitivity, ordinal, created_by) "
                "VALUES (:id, 'ontology-1', 'entity-1', 'supplier code', 'supplier code', 'string', false, '{}'::jsonb, 'internal', 0, 'creator')"
            ), {"id": str(uuid.uuid4())})
        savepoint.rollback()
        savepoint = connection.begin_nested()
        with pytest.raises(IntegrityError):
            connection.execute(text(
                "INSERT INTO entity_property_definitions "
                "(id, ontology_id, entity_id, key, normalized_key, value_type, required, constraints, sensitivity, ordinal, created_by) "
                "VALUES (:id, 'ontology-1', 'entity-1', 'Amount', 'amount', 'not-a-type', false, '{}'::jsonb, 'internal', 0, 'creator')"
            ), {"id": str(uuid.uuid4())})
        savepoint.rollback()

    with engine.connect() as connection:
        assert connection.execute(text(
            "SELECT count(*) FROM entity_property_definitions WHERE id=:id"
        ), {"id": definition_id}).scalar_one() == 1

    _run_helper(engine, "downgrade_identity_foundation")
    inspector = inspect(engine)
    assert not {"entity_property_definitions", "ontology_migration_findings"} & set(inspector.get_table_names())
    engine.dispose()

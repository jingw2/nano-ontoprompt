from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError


BACKEND_DIR = Path(__file__).resolve().parents[2]
REQUIRED = (
    BACKEND_DIR / "alembic_helpers" / "publication_release.py",
    BACKEND_DIR / "app" / "models" / "ontology_release.py",
    BACKEND_DIR / "app" / "services" / "publication" / "canonical.py",
)
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


def _manifest(**overrides):
    manifest = {
        "manifest_version": "ontology-manifest-v1",
        "compiler_version": "ontology-compiler-v1",
        "policy_compiler_version": "restricted-policy-dsl-v1",
        "aggregate_tool_schema_hash": "a" * 64,
        "ontology": {
            "id": "10000000-0000-0000-0000-000000000001",
            "name": "供应链é",
            "security_domain_id": "00000000-0000-0000-0000-000000000001",
            "description": None,
            "build_mode": "simple_llm",
        },
        "release": {"version_no": 1, "version": "v1"},
        "entities": [
            {
                "id": "e2",
                "name": "B",
                "type": "object",
                "description": "second",
                "property_definitions": [],
            },
            {
                "id": "e1",
                "name": "A",
                "type": "object",
                "description": "first",
                "property_definitions": [
                    {
                        "id": "p2",
                        "name": "amount",
                        "type": "number",
                        "required": True,
                        "default": Decimal("-0.000"),
                        "constraints": {"enum": [3, 1, 2], "maximum": Decimal("1E+2")},
                        "sensitivity": "internal",
                    },
                    {
                        "id": "p1",
                        "name": "code",
                        "type": "string",
                        "required": False,
                        "default": None,
                        "constraints": {"required": ["z", "a"]},
                        "sensitivity": "public",
                    },
                ],
            },
        ],
        "relations": [],
        "logic_rules": [],
        "state_machines": [],
        "actions": [],
        "tool_descriptors": [],
    }
    manifest.update(overrides)
    return manifest


def test_p1a_release_red_contract():
    missing = [path for path in REQUIRED if not path.exists()]
    if missing:
        pytest.fail(
            "RED_P1A_RELEASE: release manifest foundation missing: "
            + ", ".join(str(path.relative_to(BACKEND_DIR)) for path in missing)
        )


def test_manifest_canonical_bytes_sort_only_declared_collections_and_hash_raw_bytes():
    from app.services.publication.canonical import canonical_manifest, parse_json

    first = canonical_manifest(_manifest())
    reordered = _manifest(entities=list(reversed(_manifest()["entities"])))
    second = canonical_manifest(reordered)
    assert first.bytes == second.bytes
    assert first.schema_hash == hashlib.sha256(first.bytes).digest()
    projection = parse_json(first.projection)
    assert projection["entities"][0]["id"] == "e1"
    assert projection["entities"][0]["property_definitions"][0]["id"] == "p1"
    assert projection["entities"][0]["property_definitions"][0]["constraints"]["required"] == ["z", "a"]
    assert projection["entities"][0]["property_definitions"][1]["constraints"]["enum"] == [Decimal(3), Decimal(1), Decimal(2)]
    assert json.dumps(first.projection)
    assert b'"default":0' in first.bytes
    assert b'"maximum":100' in first.bytes
    assert first.bytes.decode().startswith('{"actions":[],"aggregate_tool_schema_hash"')
    assert "供应链é" in first.bytes.decode("utf-8")


def test_all_named_definition_collections_sort_by_full_stable_id_only():
    from app.services.publication.canonical import canonical_manifest, parse_json

    manifest = _manifest(
        relations=[
            {"id": "r2", "name": "A", "source_entity_id": "e1", "target_entity_id": "e2", "cardinality": "many", "direction": "out", "properties": []},
            {"id": "r1", "name": "Z", "source_entity_id": "e2", "target_entity_id": "e1", "cardinality": "one", "direction": "in", "properties": []},
        ],
        logic_rules=[
            {"id": value, "fully_qualified_label": label, "version": 1, "input_schema": {}, "output_schema": {}, "expression": ["b", "a"], "effect_classification": "read", "enabled": True}
            for value, label in (("l2", "A"), ("l1", "Z"))
        ],
        state_machines=[
            {"id": value, "label": label, "version": 1, "states": ["b", "a"], "transitions": [], "guards": [], "effects": [], "enabled": True}
            for value, label in (("s2", "A"), ("s1", "Z"))
        ],
        actions=[
            {"id": value, "fully_qualified_label": label, "version": 1, "parameter_schema": {}, "result_schema": {}, "declared_instance_effects": ["b", "a"], "risk": "low", "approval_policy": None, "enabled": True}
            for value, label in (("a2", "A"), ("a1", "Z"))
        ],
        tool_descriptors=[
            {"descriptor_id": value, "version": 1, "source_kind": "action", "source_id": "a1", "input_schema": {}, "output_schema": {}, "capability": "read", "timeout_ms": 10, "result_limit": 2, "descriptor_hash": "b" * 64}
            for value in ("t2", "t1")
        ],
    )
    projection = parse_json(canonical_manifest(manifest).projection)
    for collection, key in (
        ("relations", "id"), ("logic_rules", "id"), ("state_machines", "id"),
        ("actions", "id"), ("tool_descriptors", "descriptor_id"),
    ):
        assert [item[key] for item in projection[collection]] == sorted(item[key] for item in projection[collection])
    assert projection["logic_rules"][0]["expression"] == ["b", "a"]
    assert projection["state_machines"][0]["states"] == ["b", "a"]
    assert projection["actions"][0]["declared_instance_effects"] == ["b", "a"]


@pytest.mark.parametrize(
    ("value", "rendered"),
    [
        (Decimal("99999999999999999999.999999999999999999"), "99999999999999999999.999999999999999999"),
        (Decimal("-99999999999999999999.999999999999999999"), "-99999999999999999999.999999999999999999"),
        (Decimal("1.2300"), "1.23"),
        (Decimal("1E+3"), "1000"),
        (Decimal("-0E-18"), "0"),
        (99999999999999999999, "99999999999999999999"),
    ],
)
def test_numeric_38_18_boundaries_render_exactly(value, rendered):
    from app.services.publication.canonical import canonical_json

    assert canonical_json({"n": value}) == f'{{"n":{rendered}}}'.encode()


@pytest.mark.parametrize(
    "value",
    [
        1.0,
        float("nan"),
        float("inf"),
        Decimal("100000000000000000000"),
        Decimal("0.0000000000000000001"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("1E+1000000"),
    ],
)
def test_numeric_domain_rejects_float_nonfinite_and_out_of_numeric_38_18(value):
    from app.services.publication.canonical import CanonicalizationError, canonical_json

    with pytest.raises(CanonicalizationError):
        canonical_json({"n": value})


def test_unicode_scalar_order_preservation_surrogate_rejection_and_datetime_contract():
    from app.services.publication.canonical import CanonicalizationError, canonical_json

    assert canonical_json({"é": "e\u0301", "e": "é"}).decode() == '{"e":"é","é":"é"}'
    assert canonical_json(datetime(2026, 8, 13, 1, 2, 3, 4, tzinfo=timezone.utc)) == b'"2026-08-13T01:02:03.000004Z"'
    for invalid in (
        "\ud800",
        {"\udfff": "x"},
        datetime(2026, 8, 13),
        datetime(2026, 8, 13, tzinfo=timezone(timedelta(hours=8))),
    ):
        with pytest.raises(CanonicalizationError):
            canonical_json(invalid)


def test_json_parser_uses_decimal_and_integrity_recanonicalizes_projection():
    from app.services.publication.canonical import (
        CanonicalizationError,
        ReleaseIntegrityError,
        canonical_json,
        parse_json,
        verify_release_integrity,
    )

    parsed = parse_json('{"integer":1,"fraction":1.20}')
    assert parsed == {"integer": Decimal("1"), "fraction": Decimal("1.20")}
    manifest_bytes = canonical_json(parsed)
    schema_hash = hashlib.sha256(manifest_bytes).digest()
    verify_release_integrity(manifest_bytes, '{"fraction":1.200,"integer":1}', schema_hash)
    with pytest.raises(ReleaseIntegrityError, match="RELEASE_INTEGRITY_FAILURE"):
        verify_release_integrity(manifest_bytes, '{"fraction":2,"integer":1}', schema_hash)
    with pytest.raises(ReleaseIntegrityError, match="RELEASE_INTEGRITY_FAILURE"):
        verify_release_integrity(manifest_bytes, '{"fraction":1.2,"integer":1}', b"x" * 32)
    for duplicate in ('{"a":1,"a":2}', '{"nested":{"a":1,"a":2}}'):
        with pytest.raises(CanonicalizationError, match="duplicate JSON object key"):
            parse_json(duplicate)


def test_manifest_schema_is_closed_recursive_and_literals_are_frozen():
    from app.services.publication.canonical import Manifest

    valid = Manifest.model_validate(_manifest())
    assert valid.manifest_version == "ontology-manifest-v1"
    with pytest.raises(ValidationError):
        valid.release.version_no = 2
    invalid_cases = [
        _manifest(extra="forbidden"),
        _manifest(manifest_version="v2"),
        _manifest(aggregate_tool_schema_hash="A" * 64),
        _manifest(ontology={**_manifest()["ontology"], "secret": "forbidden"}),
        _manifest(entities=[{**_manifest()["entities"][0], "unknown": True}]),
    ]
    for invalid in invalid_cases:
        with pytest.raises(ValidationError):
            Manifest.model_validate(invalid)


def test_every_manifest_model_forbids_unknown_fields_and_required_fields_are_not_dropped():
    from app.services.publication import canonical

    model_names = (
        "OntologyIdentity", "ReleaseIdentity", "PropertyDefinition", "EntityDefinition",
        "RelationDefinition", "LogicRuleDefinition", "StateMachineDefinition",
        "ActionDefinition", "ToolDescriptor", "Manifest",
    )
    for name in model_names:
        schema = getattr(canonical, name).model_json_schema()
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])


def test_manifest_rejects_forbidden_domain_objects_and_quoted_numeric_is_not_number():
    from app.services.publication.canonical import CanonicalizationError, canonical_manifest

    for forbidden in (b"bytes", {1, 2}, object(), 1.5):
        manifest = _manifest()
        manifest["entities"][0]["description"] = forbidden
        with pytest.raises((ValidationError, CanonicalizationError)):
            canonical_manifest(manifest)
    manifest = _manifest()
    manifest["entities"][1]["property_definitions"][0]["constraints"]["maximum"] = "100.00"
    result = canonical_manifest(manifest)
    assert b'"maximum":"100.00"' in result.bytes


def test_zzz_release_orm_exact_storage_and_constraint_contract():
    script = """
import json
from app.models.ontology_release import OntologyRelease
print(json.dumps({
    'columns': list(OntologyRelease.__table__.c.keys()),
    'constraints': [constraint.name for constraint in OntologyRelease.__table__.constraints],
    'indexes': {index.name: index.unique for index in OntologyRelease.__table__.indexes},
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=BACKEND_DIR, capture_output=True, text=True, check=True
    )
    metadata = json.loads(result.stdout)
    assert set(metadata["columns"]) == {
        "id", "ontology_id", "version_no", "version", "manifest_bytes",
        "manifest_projection", "schema_hash", "created_by", "created_at",
    }
    constraints = set(metadata["constraints"])
    assert constraints >= {
        "uq_ontology_releases_ontology_version_no",
        "ck_ontology_releases_schema_hash_length",
        "ck_ontology_releases_manifest_integrity",
        "ck_ontology_releases_id_uuid",
        "ck_ontology_releases_version_no",
    }
    assert metadata["indexes"]["ix_ontology_releases_schema_hash"] is False


class _FakeOp:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
        return record


def test_migration_helper_upgrade_and_downgrade_order(monkeypatch):
    from alembic_helpers import publication_release

    fake = _FakeOp()
    monkeypatch.setattr(publication_release, "op", fake)
    publication_release.upgrade_release_foundation()
    upgrade_names = [name for name, _, _ in fake.calls]
    assert upgrade_names.index("create_table") < upgrade_names.index("add_column") < upgrade_names.index("create_foreign_key")
    trigger_sql = "\n".join(str(args[0]) for name, args, _ in fake.calls if name == "execute")
    assert "validate_ontology_release_domain" in trigger_sql
    assert "ontology_releases_immutable" in trigger_sql

    fake.calls.clear()
    publication_release.downgrade_release_foundation()
    downgrade_names = [name for name, _, _ in fake.calls]
    assert downgrade_names.index("drop_constraint") < downgrade_names.index("drop_column") < downgrade_names.index("drop_table")


@pytest.fixture
def release_schema():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "p1a_release_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    yield schema
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def test_postgresql_numeric_round_trip_and_helper_catalog(release_schema):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic_helpers import publication_release
    from app.models.ontology_release import OntologyRelease
    from app.services.publication.canonical import canonical_manifest, verify_release_integrity

    url = f"{TEST_DATABASE_URL}?options=-csearch_path%3D{release_schema}%2Cpublic"
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE security_domains (id varchar(36) PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE users (id varchar PRIMARY KEY, security_domain_id varchar(36) NOT NULL)"))
        connection.execute(text("CREATE UNIQUE INDEX uq_users_id_security_domain ON users(id, security_domain_id)"))
        connection.execute(text("CREATE TABLE ontology_projects (id varchar PRIMARY KEY, security_domain_id varchar(36) NOT NULL)"))
        connection.execute(text("CREATE UNIQUE INDEX uq_ontology_projects_id_security_domain ON ontology_projects(id, security_domain_id)"))
        context = MigrationContext.configure(connection)
        publication_release.op = Operations(context)
        publication_release.upgrade_release_foundation()
        numeric = Decimal("99999999999999999999.999999999999999999")
        assert connection.execute(text("SELECT CAST(:value AS numeric(38,18))"), {"value": numeric}).scalar_one() == numeric
        catalog = inspect(connection)
        assert catalog.has_table("ontology_releases")
        assert "latest_published_release_id" in {column["name"] for column in catalog.get_columns("ontology_projects")}
        triggers = {row[0] for row in connection.execute(text("SELECT tgname FROM pg_trigger WHERE tgrelid='ontology_releases'::regclass AND NOT tgisinternal"))}
        assert triggers >= {"ontology_releases_validate_domain", "ontology_releases_immutable"}
        default_domain = "00000000-0000-0000-0000-000000000001"
        other_domain = "00000000-0000-0000-0000-000000000002"
        release_id = "20000000-0000-0000-0000-000000000001"
        connection.execute(text("INSERT INTO security_domains(id) VALUES (:one),(:two)"), {"one": default_domain, "two": other_domain})
        connection.execute(text("INSERT INTO users(id,security_domain_id) VALUES ('creator',:domain),('other',:other)"), {"domain": default_domain, "other": other_domain})
        connection.execute(text("INSERT INTO ontology_projects(id,security_domain_id) VALUES ('ontology',:domain)"), {"domain": default_domain})
        boundary_manifest = _manifest()
        boundary_manifest["entities"][1]["property_definitions"][0]["constraints"] = {
            "maximum": Decimal("99999999999999999999.999999999999999999"),
            "minimum": Decimal("-99999999999999999999.999999999999999999"),
            "fraction": Decimal("1.2300"),
            "exponent": Decimal("1E+3"),
            "negative_zero": Decimal("-0E-18"),
            "timestamp": datetime(2026, 8, 13, 1, 2, 3, 4, tzinfo=timezone.utc),
        }
        canonical = canonical_manifest(boundary_manifest)
        manifest_bytes = canonical.bytes
        schema_hash = canonical.schema_hash
        connection.execute(
            OntologyRelease.__table__.insert(),
            {
                "id": release_id,
                "ontology_id": "ontology",
                "version_no": 1,
                "version": "v1",
                "manifest_bytes": manifest_bytes,
                "manifest_projection": canonical.projection,
                "schema_hash": schema_hash,
                "created_by": "creator",
            },
        )
        stored_projection = connection.execute(text("SELECT manifest_projection::text FROM ontology_releases WHERE id=:id"), {"id": release_id}).scalar_one()
        verify_release_integrity(manifest_bytes, stored_projection, schema_hash)
        connection.execute(text("UPDATE ontology_projects SET latest_published_release_id=:id WHERE id='ontology'"), {"id": release_id})
        assert connection.execute(text("SELECT latest_published_release_id FROM ontology_projects WHERE id='ontology'")).scalar_one() == release_id
        foreign_keys = {fk["name"]: fk for fk in inspect(connection).get_foreign_keys("ontology_releases")}
        assert foreign_keys["fk_ontology_releases_ontology"]["options"]["ondelete"] == "RESTRICT"
        assert foreign_keys["fk_ontology_releases_creator"]["options"]["ondelete"] == "RESTRICT"
        for statement, parameters, message in (
            (
                "INSERT INTO ontology_releases(id,ontology_id,version_no,version,manifest_bytes,manifest_projection,schema_hash,created_by) "
                "VALUES ('20000000-0000-0000-0000-000000000002','ontology',2,'v2',:bytes,CAST(:projection AS jsonb),:hash,'other')",
                {"bytes": manifest_bytes, "projection": canonical.projection, "hash": schema_hash},
                "SECURITY_DOMAIN_MISMATCH",
            ),
            (
                "INSERT INTO ontology_releases(id,ontology_id,version_no,version,manifest_bytes,manifest_projection,schema_hash,created_by) "
                "VALUES ('20000000-0000-0000-0000-000000000003','ontology',3,'v3',:bytes,CAST(:projection AS jsonb),:hash,'creator')",
                {"bytes": manifest_bytes, "projection": canonical.projection, "hash": b"z" * 32},
                "ck_ontology_releases_manifest_integrity",
            ),
        ):
            savepoint = connection.begin_nested()
            with pytest.raises((DBAPIError, IntegrityError), match=message):
                connection.execute(text(statement), parameters)
            savepoint.rollback()
        for statement in (
            "UPDATE ontology_releases SET version='changed' WHERE id=:id",
            "DELETE FROM ontology_releases WHERE id=:id",
        ):
            savepoint = connection.begin_nested()
            with pytest.raises(DBAPIError, match="RELEASE_IMMUTABLE"):
                connection.execute(text(statement), {"id": release_id})
            savepoint.rollback()
        assert connection.execute(text("SELECT count(*) FROM ontology_releases WHERE id=:id"), {"id": release_id}).scalar_one() == 1
        hostile_schema = "hostile_" + uuid.uuid4().hex
        connection.execute(text(f'CREATE SCHEMA "{hostile_schema}"'))
        connection.execute(text(f'CREATE TABLE "{hostile_schema}".users (id varchar PRIMARY KEY, security_domain_id varchar(36))'))
        connection.execute(text(f'CREATE TABLE "{hostile_schema}".ontology_projects (id varchar PRIMARY KEY, security_domain_id varchar(36))'))
        connection.execute(text(f'INSERT INTO "{hostile_schema}".users VALUES (\'other\',\'00000000-0000-0000-0000-000000000009\')'))
        connection.execute(text(f'INSERT INTO "{hostile_schema}".ontology_projects VALUES (\'ontology\',\'00000000-0000-0000-0000-000000000009\')'))
        connection.execute(text(f'SET LOCAL search_path TO "{hostile_schema}", "{release_schema}", public'))
        savepoint = connection.begin_nested()
        with pytest.raises(DBAPIError, match="SECURITY_DOMAIN_MISMATCH"):
            connection.execute(text(
                f'INSERT INTO "{release_schema}".ontology_releases(id,ontology_id,version_no,version,manifest_bytes,manifest_projection,schema_hash,created_by) '
                "VALUES ('20000000-0000-0000-0000-000000000004','ontology',4,'v4',:bytes,CAST(:projection AS jsonb),:hash,'other')"
            ), {"bytes": manifest_bytes, "projection": canonical.projection, "hash": schema_hash})
        savepoint.rollback()
        assert connection.execute(text(f'SELECT count(*) FROM "{release_schema}".ontology_releases WHERE version_no=4')).scalar_one() == 0
        connection.execute(text(f'SET LOCAL search_path TO "{release_schema}", public'))
        connection.execute(text("UPDATE ontology_projects SET latest_published_release_id=NULL WHERE id='ontology'"))
        publication_release.downgrade_release_foundation()
        assert not inspect(connection).has_table("ontology_releases")
        assert "latest_published_release_id" not in {column["name"] for column in inspect(connection).get_columns("ontology_projects")}
    engine.dispose()

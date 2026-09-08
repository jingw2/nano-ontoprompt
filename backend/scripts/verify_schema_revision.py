"""Read-only verification of the database revision and critical schema."""

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping

try:
    from .check_python_version import require_supported_python
except ImportError:  # Direct script execution.
    from check_python_version import require_supported_python

require_supported_python()

from sqlalchemy import create_engine, inspect, text  # noqa: E402


BUILD_MANIFEST_INVALID = "BUILD_MANIFEST_INVALID"
DATABASE_REVISION_MISMATCH = "DATABASE_REVISION_MISMATCH"
DATABASE_SCHEMA_DRIFT = "DATABASE_SCHEMA_DRIFT"
_REVISION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class ManifestError(ValueError):
    pass


class SchemaVerificationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ForeignKeyContract:
    columns: tuple[str, ...]
    referred_schema: str | None
    referred_table: str
    referred_columns: tuple[str, ...]
    ondelete: str | None


@dataclass(frozen=True)
class CheckContract:
    name: str
    sql: str


@dataclass(frozen=True)
class IndexContract:
    name: str
    unique: bool
    expressions: tuple[str, ...]


@dataclass(frozen=True)
class TriggerContract:
    name: str
    definition: str
    enabled: str


@dataclass(frozen=True)
class TableContract:
    columns: frozenset[str]
    primary_key: tuple[str, ...]
    unique: frozenset[tuple[str, ...]]
    foreign_keys: frozenset[ForeignKeyContract]
    checks: frozenset[CheckContract]
    indexes: frozenset[IndexContract]
    triggers: frozenset[TriggerContract]


@dataclass(frozen=True)
class SchemaManifest:
    accepted_revisions: frozenset[str]
    critical_tables: Mapping[str, TableContract]


_SCHEMA_KEYS = {
    "schema_contract_version",
    "schema_revision",
    "compatible_schema_revisions",
    "critical_schema",
}
_ENVELOPE_KEYS = {
    "manifest_version",
    "image_digest",
    "source_digest",
    "runtime_artifact_tuple",
    "python_lock_hash",
    "dependency_lock_hash",
    "signer_identity",
    "signature",
    "schema_contract",
}


def _invalid(detail):
    raise ManifestError(f"{BUILD_MANIFEST_INVALID}: {detail}")


def normalize_sql(value):
    return " ".join(value.strip().rstrip(";").split())


def _strings(value, label, allow_empty=True):
    if (
        not isinstance(value, list)
        or (not allow_empty and not value)
        or any(not isinstance(item, str) or not item for item in value)
        or len(set(value)) != len(value)
    ):
        _invalid(f"{label} must be a unique string list")
    return tuple(value)


def _objects(value, label):
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        _invalid(f"{label} must be an object list")
    return value


def _unique_contracts(values, label, identity):
    if len(values) != len(set(values)) or len(values) != len({identity(item) for item in values}):
        _invalid(f"{label} must not contain duplicate definitions")
    return frozenset(values)


def _exact_keys(value, allowed, label):
    unexpected = set(value) - set(allowed)
    if unexpected:
        _invalid(f"{label} has unexpected keys")


def _table_contract(value):
    allowed = {"columns", "primary_key", "unique", "foreign_keys", "checks", "indexes", "triggers"}
    if not isinstance(value, dict):
        _invalid("table contract must be an object")
    _exact_keys(value, allowed, "table contract")
    if set(value) != allowed:
        _invalid("table contract must declare every contract kind")
    columns = frozenset(_strings(value["columns"], "columns", allow_empty=False))
    primary_key = _strings(value["primary_key"], "primary_key")
    unique_items = value["unique"]
    if not isinstance(unique_items, list):
        _invalid("unique must be a list")
    unique_values = [
        _strings(item, "unique columns", allow_empty=False)
        for item in unique_items
    ]
    unique = _unique_contracts(unique_values, "unique", lambda item: item)
    foreign_keys = []
    for item in _objects(value["foreign_keys"], "foreign_keys"):
        _exact_keys(item, {"columns", "referred_schema", "referred_table", "referred_columns", "ondelete"}, "foreign key")
        columns_local = _strings(item.get("columns"), "foreign key columns", allow_empty=False)
        columns_remote = _strings(item.get("referred_columns"), "foreign key referred_columns", allow_empty=False)
        referred_table = item.get("referred_table")
        referred_schema = item.get("referred_schema")
        ondelete = item.get("ondelete")
        if not isinstance(referred_table, str) or not referred_table:
            _invalid("foreign key referred_table is required")
        if referred_schema is not None and (not isinstance(referred_schema, str) or not referred_schema):
            _invalid("foreign key referred_schema is invalid")
        if ondelete is not None and (not isinstance(ondelete, str) or not ondelete):
            _invalid("foreign key ondelete is invalid")
        foreign_keys.append(ForeignKeyContract(
            columns_local,
            referred_schema,
            referred_table,
            columns_remote,
            ondelete.upper() if ondelete else None,
        ))
    checks = []
    for item in _objects(value["checks"], "checks"):
        _exact_keys(item, {"name", "sql"}, "check")
        if not isinstance(item.get("name"), str) or not item["name"] or not isinstance(item.get("sql"), str) or not item["sql"]:
            _invalid("check name/sql are required")
        checks.append(CheckContract(item["name"], normalize_sql(item["sql"])))
    indexes = []
    for item in _objects(value["indexes"], "indexes"):
        _exact_keys(item, {"name", "unique", "expressions"}, "index")
        if not isinstance(item.get("name"), str) or not item["name"] or not isinstance(item.get("unique"), bool):
            _invalid("index name/unique are required")
        expressions = tuple(normalize_sql(item) for item in _strings(item.get("expressions"), "index expressions", allow_empty=False))
        indexes.append(IndexContract(item["name"], item["unique"], expressions))
    triggers = []
    for item in _objects(value["triggers"], "triggers"):
        _exact_keys(item, {"name", "definition", "enabled"}, "trigger")
        if any(not isinstance(item.get(key), str) or not item[key] for key in ("name", "definition", "enabled")):
            _invalid("trigger name/definition/enabled are required")
        if item["enabled"] not in {"O", "D", "R", "A"}:
            _invalid("trigger enabled must be O, D, R, or A")
        triggers.append(TriggerContract(item["name"], normalize_sql(item["definition"]), item["enabled"]))
    foreign_keys = _unique_contracts(foreign_keys, "foreign_keys", lambda item: (item.columns, item.referred_schema, item.referred_table))
    checks = _unique_contracts(checks, "checks", lambda item: item.name)
    indexes = _unique_contracts(indexes, "indexes", lambda item: item.name)
    triggers = _unique_contracts(triggers, "triggers", lambda item: item.name)
    declared_local_columns = set(primary_key)
    declared_local_columns.update(column for item in unique for column in item)
    declared_local_columns.update(column for item in foreign_keys for column in item.columns)
    if not declared_local_columns <= columns:
        _invalid("constraint columns must be declared in columns")
    contract = TableContract(columns, primary_key, unique, foreign_keys, checks, indexes, triggers)
    if not any((columns, primary_key, unique, foreign_keys, checks, indexes, triggers)):
        _invalid("table contract must declare a critical object")
    return contract


def load_manifest(path):
    try:
        payload = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"{BUILD_MANIFEST_INVALID}: unreadable manifest") from exc
    if not isinstance(payload, dict):
        _invalid("manifest must be an object")
    if "schema_contract" in payload:
        _exact_keys(payload, _ENVELOPE_KEYS, "build manifest")
        required_envelope = _ENVELOPE_KEYS
        if not required_envelope <= set(payload):
            _invalid("build manifest metadata is incomplete")
        if payload.get("manifest_version") != 1:
            _invalid("manifest_version must equal 1")
        for key in required_envelope - {"manifest_version", "runtime_artifact_tuple", "schema_contract"}:
            if not isinstance(payload.get(key), str) or not payload[key]:
                _invalid(f"build manifest {key} is required")
        if (
            not isinstance(payload.get("runtime_artifact_tuple"), list)
            or not payload["runtime_artifact_tuple"]
            or any(not isinstance(item, str) or not item for item in payload["runtime_artifact_tuple"])
        ):
            _invalid("build manifest runtime_artifact_tuple is required")
        payload = payload["schema_contract"]
        if not isinstance(payload, dict):
            _invalid("schema_contract must be an object")
    _exact_keys(payload, _SCHEMA_KEYS, "schema contract")
    if payload.get("schema_contract_version") != 1:
        _invalid("schema_contract_version must equal 1")
    exact = payload.get("schema_revision")
    compatible = payload.get("compatible_schema_revisions")
    if (exact is None) == (compatible is None):
        _invalid("declare exactly one revision mode")
    revisions = [exact] if exact is not None else compatible
    if (
        not isinstance(revisions, list)
        or not revisions
        or any(not isinstance(item, str) or not _REVISION.fullmatch(item) for item in revisions)
        or len(set(revisions)) != len(revisions)
        or any(item == "head" for item in revisions)
    ):
        _invalid("revisions must be a non-empty closed set of exact identifiers")
    critical = payload.get("critical_schema")
    if not isinstance(critical, dict):
        _invalid("critical_schema must be an object")
    _exact_keys(critical, {"tables"}, "critical_schema")
    tables = critical.get("tables")
    if not isinstance(tables, dict) or not tables:
        _invalid("critical_schema.tables must be a non-empty object")
    normalized = {}
    for table_name, contract in tables.items():
        if not isinstance(table_name, str) or not table_name:
            _invalid("critical table name is invalid")
        normalized[table_name] = _table_contract(contract)
    return SchemaManifest(frozenset(revisions), MappingProxyType(normalized))


def _index_expressions(index):
    expressions = index.get("expressions") or index.get("column_names") or []
    return tuple(normalize_sql(item) for item in expressions if item is not None)


def verify_connection(connection, manifest):
    inspector = inspect(connection)
    if "alembic_version" not in inspector.get_table_names():
        raise SchemaVerificationError(f"{DATABASE_REVISION_MISMATCH}: alembic_version table is missing")
    heads = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    if len(heads) != 1 or heads[0] not in manifest.accepted_revisions:
        raise SchemaVerificationError(f"{DATABASE_REVISION_MISMATCH}: database head is not accepted")
    table_names = set(inspector.get_table_names())
    for table_name, required in manifest.critical_tables.items():
        if table_name not in table_names:
            raise SchemaVerificationError(f"{DATABASE_SCHEMA_DRIFT}: required table {table_name!r} is missing")
        actual_columns = {column["name"] for column in inspector.get_columns(table_name)}
        if not required.columns <= actual_columns:
            raise SchemaVerificationError(f"{DATABASE_SCHEMA_DRIFT}: columns differ for {table_name!r}")
        actual_pk = tuple(inspector.get_pk_constraint(table_name).get("constrained_columns") or [])
        if required.primary_key and required.primary_key != actual_pk:
            raise SchemaVerificationError(f"{DATABASE_SCHEMA_DRIFT}: primary key differs for {table_name!r}")
        actual_unique = {tuple(item.get("column_names") or []) for item in inspector.get_unique_constraints(table_name)}
        if not required.unique <= actual_unique:
            raise SchemaVerificationError(f"{DATABASE_SCHEMA_DRIFT}: unique constraints differ for {table_name!r}")
        actual_fks = set()
        for item in inspector.get_foreign_keys(table_name):
            options = item.get("options") or {}
            actual_fks.add(ForeignKeyContract(
                tuple(item.get("constrained_columns") or []),
                item.get("referred_schema"),
                item.get("referred_table"),
                tuple(item.get("referred_columns") or []),
                options.get("ondelete"),
            ))
        if not required.foreign_keys <= actual_fks:
            raise SchemaVerificationError(f"{DATABASE_SCHEMA_DRIFT}: foreign keys differ for {table_name!r}")
        actual_checks = {
            CheckContract(item.get("name"), normalize_sql(item.get("sqltext") or ""))
            for item in inspector.get_check_constraints(table_name)
        }
        if not required.checks <= actual_checks:
            raise SchemaVerificationError(f"{DATABASE_SCHEMA_DRIFT}: checks differ for {table_name!r}")
        actual_indexes = {
            IndexContract(item.get("name"), bool(item.get("unique")), _index_expressions(item))
            for item in inspector.get_indexes(table_name)
        }
        if not required.indexes <= actual_indexes:
            raise SchemaVerificationError(f"{DATABASE_SCHEMA_DRIFT}: indexes differ for {table_name!r}")
        actual_triggers = {
            TriggerContract(row.name, normalize_sql(row.definition), row.enabled)
            for row in connection.execute(text(
                "SELECT tg.tgname AS name, pg_get_triggerdef(tg.oid, true) AS definition, tg.tgenabled AS enabled "
                "FROM pg_trigger tg JOIN pg_class c ON c.oid=tg.tgrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=current_schema() AND c.relname=:table_name AND NOT tg.tgisinternal"
            ), {"table_name": table_name})
        }
        if not required.triggers <= actual_triggers:
            raise SchemaVerificationError(f"{DATABASE_SCHEMA_DRIFT}: triggers differ for {table_name!r}")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-manifest", required=True)
    args = parser.parse_args(argv)
    manifest = load_manifest(args.build_manifest)
    database_url = os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        raise SystemExit(f"{DATABASE_REVISION_MISMATCH}: database URL is missing")
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                verify_connection(connection, manifest)
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        main()
    except (ManifestError, SchemaVerificationError) as exc:
        raise SystemExit(str(exc)) from exc

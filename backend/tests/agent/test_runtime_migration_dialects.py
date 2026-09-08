"""Dialect regressions for the unmerged Runtime identity/plan migrations."""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    JSON,
    MetaData,
    String,
    Table,
    create_engine,
    event,
    inspect,
    insert,
    text,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import IntegrityError


BACKEND_DIR = Path(__file__).resolve().parents[2]
MIGRATION_DIR = BACKEND_DIR / "alembic" / "versions"
DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"


def _load_migration(revision: str):
    path = MIGRATION_DIR / f"{revision}.py"
    spec = importlib.util.spec_from_file_location(f"migration_{revision}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _render_mysql_upgrade(revision: str) -> str:
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect=mysql.dialect(),
        opts={
            "as_sql": True,
            "literal_binds": True,
            "output_buffer": output,
        },
    )
    migration = _load_migration(revision)
    migration.op = Operations(context)
    migration.upgrade()
    return output.getvalue()


@pytest.mark.parametrize("revision", ["0029_runtime_identity", "0030_runtime_plans"])
def test_runtime_migrations_render_with_mysql_dialect(revision: str):
    """MySQL SQL is literal-renderable and contains no PostgreSQL syntax."""
    rendered = _render_mysql_upgrade(revision)
    assert "%s" not in rendered
    assert "?" not in rendered
    assert "::json" not in rendered.lower()
    assert " ~ " not in rendered
    assert "REGEXP" in rendered
    if revision == "0029_runtime_identity":
        assert (
            "UPDATE oauth_clients SET security_domain_id="
            f"'{DEFAULT_DOMAIN}'"
        ) in rendered
        assert "SET allowed_audiences=(JSON_ARRAY())" in rendered
        assert "SET capability_names=(JSON_ARRAY())" in rendered
    else:
        assert "DEFAULT (JSON_OBJECT())" in rendered
        assert "DEFAULT (JSON_ARRAY())" in rendered


def _run_migration(connection, revision: str) -> None:
    migration = _load_migration(revision)
    migration.op = Operations(MigrationContext.configure(connection))
    migration.upgrade()


def _run_downgrade(connection, revision: str) -> None:
    migration = _load_migration(revision)
    migration.op = Operations(MigrationContext.configure(connection))
    migration.downgrade()


SQLITE_UUID_GLOB = (
    "[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]-"
    "[0-9a-f][0-9a-f][0-9a-f][0-9a-f]-"
    "[0-9a-f][0-9a-f][0-9a-f][0-9a-f]-"
    "[0-9a-f][0-9a-f][0-9a-f][0-9a-f]-"
    "[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]"
)
LEGACY_USER_ID = "10000000-0000-0000-0000-000000000001"
LEGACY_CLIENT_ID = "20000000-0000-0000-0000-000000000001"


def test_runtime_identity_migration_preserves_sqlite_legacy_schema_and_downgrades():
    """0029 handles a representative legacy OAuth table in both directions."""
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    metadata = MetaData()
    security_domains = Table(
        "security_domains", metadata,
        Column("id", String(36), primary_key=True),
    )
    users = Table(
        "users", metadata,
        Column("id", String(36), primary_key=True),
        Column(
            "security_domain_id", String(36),
            ForeignKey("security_domains.id", ondelete="RESTRICT"),
            nullable=False,
        ),
    )
    oauth_clients = Table(
        "oauth_clients", metadata,
        Column("id", String(36), primary_key=True),
        Column("client_name", String(200), nullable=False),
        Column("redirect_uris", JSON, nullable=False),
        Column("allowed_scopes", JSON, nullable=False),
        Column("is_active", Boolean, nullable=False, server_default=text("1")),
        Column(
            "created_by", String(36),
            ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        Column(
            "created_at", DateTime(timezone=True),
            nullable=False, server_default=text("CURRENT_TIMESTAMP"),
        ),
        CheckConstraint(
            f"id GLOB '{SQLITE_UUID_GLOB}'",
            name="ck_oauth_clients_id_uuid",
        ),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(insert(security_domains), {"id": DEFAULT_DOMAIN})
        connection.execute(insert(users), {
            "id": LEGACY_USER_ID, "security_domain_id": DEFAULT_DOMAIN,
        })
        connection.execute(insert(oauth_clients), {
            "id": LEGACY_CLIENT_ID, "client_name": "legacy", "redirect_uris": [],
            "allowed_scopes": [], "created_by": LEGACY_USER_ID,
        })
        before = connection.execute(text(
            "SELECT id, client_name, redirect_uris, allowed_scopes, is_active, "
            "created_by, created_at FROM oauth_clients"
        )).one()
        assert before.is_active == 1
        assert before.created_at is not None
        before_fks = inspect(connection).get_foreign_keys("oauth_clients")
        assert any(
            fk["constrained_columns"] == ["created_by"]
            and fk["referred_table"] == "users"
            for fk in before_fks
        )
        fk_signature = lambda fks: sorted(
            (
                tuple(fk["constrained_columns"]),
                fk["referred_table"],
                tuple(fk["referred_columns"]),
                (fk.get("options") or {}).get("ondelete"),
            )
            for fk in fks
        )
        before_checks = {
            check["name"]: check["sqltext"]
            for check in inspect(connection).get_check_constraints("oauth_clients")
        }
        assert "ck_oauth_clients_id_uuid" in before_checks

        _run_migration(connection, "0029_runtime_identity")
        client = connection.execute(
            text(
                "SELECT id, client_name, redirect_uris, allowed_scopes, is_active, "
                "created_by, created_at, security_domain_id, allowed_audiences, "
                "capability_names FROM oauth_clients"
            )
        ).one()
        assert tuple(client[:7]) == tuple(before)
        assert client.security_domain_id == DEFAULT_DOMAIN
        assert json.loads(client.allowed_audiences) == []
        assert json.loads(client.capability_names) == []
        columns = {
            column["name"]: column
            for column in inspect(connection).get_columns("oauth_clients")
        }
        assert set(columns) >= {
            "security_domain_id", "allowed_audiences", "capability_names",
        }
        assert columns["security_domain_id"]["nullable"] is False
        assert columns["allowed_audiences"]["nullable"] is False
        assert columns["capability_names"]["nullable"] is False
        assert columns["security_domain_id"]["default"] == f"'{DEFAULT_DOMAIN}'"
        assert columns["allowed_audiences"]["default"] == "'[]'"
        assert columns["capability_names"]["default"] == "'[]'"
        assert columns["created_at"]["default"] == "CURRENT_TIMESTAMP"
        assert columns["is_active"]["default"] == "1"
        fks = inspect(connection).get_foreign_keys("oauth_clients")
        assert any(
            fk["constrained_columns"] == ["created_by"]
            and fk["referred_table"] == "users"
            for fk in fks
        )
        assert any(
            fk["constrained_columns"] == ["security_domain_id"]
            and fk["referred_table"] == "security_domains"
            for fk in fks
        )
        after_checks = {
            check["name"]: check["sqltext"]
            for check in inspect(connection).get_check_constraints("oauth_clients")
        }
        assert {
            name: after_checks[name]
            for name in before_checks
        } == before_checks
        assert fk_signature(fks) == sorted(fk_signature(before_fks) + [
            (("security_domain_id",), "security_domains", ("id",), "RESTRICT")
        ])
        constraints = {
            constraint["name"]
            for constraint in inspect(connection).get_check_constraints("runtime_delegated_credentials")
        }
        assert {
            "ck_runtime_delegated_credentials_id_uuid",
            "ck_runtime_delegated_credentials_status",
        } <= constraints

        with pytest.raises(IntegrityError):
            connection.execute(text(
                "INSERT INTO runtime_delegated_credentials "
                "(id, token_hash, client_id, user_id, audience, expires_at) "
                "VALUES (:id, :token_hash, :client_id, :user_id, :audience, "
                "CURRENT_TIMESTAMP)"
            ), {
                "id": "not-a-uuid", "token_hash": "hash-invalid",
                "client_id": LEGACY_CLIENT_ID, "user_id": LEGACY_USER_ID,
                "audience": "test",
            })

        _run_downgrade(connection, "0029_runtime_identity")
        assert "runtime_delegated_credentials" not in inspect(connection).get_table_names()
        downgraded_columns = {
            column["name"]: column
            for column in inspect(connection).get_columns("oauth_clients")
        }
        assert set(downgraded_columns) == {
            "id", "client_name", "redirect_uris", "allowed_scopes", "is_active",
            "created_by", "created_at",
        }
        assert downgraded_columns["created_at"]["default"] == "CURRENT_TIMESTAMP"
        assert downgraded_columns["is_active"]["default"] == "1"
        downgraded_checks = {
            check["name"]: check["sqltext"]
            for check in inspect(connection).get_check_constraints("oauth_clients")
        }
        assert downgraded_checks == before_checks
        downgraded_fks = inspect(connection).get_foreign_keys("oauth_clients")
        assert fk_signature(downgraded_fks) == fk_signature(before_fks)
        restored = connection.execute(text(
            "SELECT id, client_name, redirect_uris, allowed_scopes, is_active, "
            "created_by, created_at FROM oauth_clients"
        )).one()
        assert tuple(restored) == tuple(before)


def test_runtime_plans_migration_adds_sqlite_constraints():
    """0030 keeps its integrity checks on SQLite too."""
    engine = create_engine("sqlite://")
    metadata = MetaData()
    Table("oauth_clients", metadata, Column("id", String(36), primary_key=True))
    Table("users", metadata, Column("id", String(36), primary_key=True))
    Table("semantic_snapshots", metadata, Column("id", String(36), primary_key=True))
    Table("ontology_releases", metadata, Column("id", String(36), primary_key=True))
    Table("actions", metadata, Column("id", String(36), primary_key=True))
    metadata.create_all(engine)
    with engine.begin() as connection:
        _run_migration(connection, "0030_runtime_plans")
        constraints = {
            constraint["name"]
            for constraint in inspect(connection).get_check_constraints("runtime_plans")
        }
        assert {
            "ck_runtime_plans_id_uuid",
            "ck_runtime_plans_plan_hash",
            "ck_runtime_plans_before_image_hash",
            "ck_runtime_plans_version_hash",
        } <= constraints

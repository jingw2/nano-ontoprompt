"""Dialect regressions for the unmerged Runtime identity/plan migrations."""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Boolean, Column, JSON, MetaData, String, Table, create_engine, insert, inspect, text
from sqlalchemy.dialects import mysql


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
        opts={"as_sql": True, "output_buffer": output},
    )
    migration = _load_migration(revision)
    migration.op = Operations(context)
    migration.upgrade()
    return output.getvalue()


@pytest.mark.parametrize("revision", ["0029_runtime_identity", "0030_runtime_plans"])
def test_runtime_migrations_render_with_mysql_dialect(revision: str):
    """MySQL DDL must not contain PostgreSQL casts/operators."""
    rendered = _render_mysql_upgrade(revision)
    assert "::json" not in rendered.lower()
    assert " ~ " not in rendered
    assert "REGEXP" in rendered


def _run_migration(connection, revision: str) -> None:
    migration = _load_migration(revision)
    migration.op = Operations(MigrationContext.configure(connection))
    migration.upgrade()


def test_runtime_migrations_backfill_and_enforce_constraints_on_sqlite():
    """SQLite exercises the same migration path without a live MySQL service."""
    engine = create_engine("sqlite://")
    metadata = MetaData()
    security_domains = Table(
        "security_domains", metadata,
        Column("id", String(36), primary_key=True),
    )
    users = Table("users", metadata, Column("id", String(36), primary_key=True))
    oauth_clients = Table(
        "oauth_clients", metadata,
        Column("id", String(36), primary_key=True),
        Column("client_name", String(200), nullable=False),
        Column("redirect_uris", JSON, nullable=False),
        Column("allowed_scopes", JSON, nullable=False),
        Column("is_active", Boolean, nullable=False),
        Column("created_by", String(36), nullable=False),
    )
    semantic_snapshots = Table("semantic_snapshots", metadata, Column("id", String(36), primary_key=True))
    ontology_releases = Table("ontology_releases", metadata, Column("id", String(36), primary_key=True))
    actions = Table("actions", metadata, Column("id", String(36), primary_key=True))
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(insert(security_domains), {"id": DEFAULT_DOMAIN})
        connection.execute(insert(users), {"id": "legacy-user"})
        connection.execute(insert(oauth_clients), {
            "id": "legacy-client", "client_name": "legacy", "redirect_uris": [],
            "allowed_scopes": [], "is_active": True, "created_by": "legacy-user",
        })
        _run_migration(connection, "0029_runtime_identity")
        client = connection.execute(
            text("SELECT security_domain_id, allowed_audiences, capability_names FROM oauth_clients")
        ).one()
        assert client.security_domain_id == DEFAULT_DOMAIN
        assert json.loads(client.allowed_audiences) == []
        assert json.loads(client.capability_names) == []
        assert {column["name"] for column in inspect(connection).get_columns("oauth_clients")} >= {
            "security_domain_id", "allowed_audiences", "capability_names",
        }
        constraints = {
            constraint["name"]
            for constraint in inspect(connection).get_check_constraints("runtime_delegated_credentials")
        }
        assert {
            "ck_runtime_delegated_credentials_id_uuid",
            "ck_runtime_delegated_credentials_status",
        } <= constraints

        metadata.create_all(connection)
        _run_migration(connection, "0030_runtime_plans")
        plan_constraints = {
            constraint["name"]
            for constraint in inspect(connection).get_check_constraints("runtime_plans")
        }
        assert {
            "ck_runtime_plans_id_uuid",
            "ck_runtime_plans_plan_hash",
            "ck_runtime_plans_before_image_hash",
            "ck_runtime_plans_version_hash",
        } <= plan_constraints

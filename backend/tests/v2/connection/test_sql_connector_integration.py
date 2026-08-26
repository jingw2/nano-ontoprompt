"""SQLConnector against a real, disposable Postgres schema — no mocks.
Complements test_sql_connector.py's mocked unit tests with real behavior for
test_connection, list_resources, pull_full, pull_sample, and a real
watermark-based pull_delta."""
import os
import uuid
from urllib.parse import quote

import pytest
from sqlalchemy import create_engine, text

from app.services.connection.sql_connector import SQLConnector

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


def _scoped_url(schema: str) -> str:
    return f"{TEST_DATABASE_URL}?options={quote(f'-csearch_path={schema},public', safe='-=,')}"


@pytest.fixture
def pg_schema():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "sqlconn_it_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'''
            CREATE TABLE "{schema}".t_customers (
                id INT PRIMARY KEY,
                name TEXT NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
        '''))
        connection.execute(text(f'''
            INSERT INTO "{schema}".t_customers (id, name, updated_at) VALUES
                (1, 'Alice', '2026-01-01 00:00:00'),
                (2, 'Bob', '2026-02-01 00:00:00'),
                (3, 'Carol', '2026-03-01 00:00:00')
        '''))
    yield _scoped_url(schema)
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def test_connection_succeeds_against_real_schema(pg_schema):
    connector = SQLConnector({"connection_string": pg_schema})
    assert connector.test_connection() is True


def test_list_resources_finds_the_seeded_table(pg_schema):
    connector = SQLConnector({"connection_string": pg_schema})
    assert "t_customers" in connector.list_resources()


@pytest.mark.xfail(
    reason="Known bug: sql_connector.py:75 calls pd.read_sql(query, "
    "self._get_engine()) — a raw SQLAlchemy Engine. pandas>=2.2 is "
    "unpinned in requirements.txt and currently resolves to pandas 3.0.3, "
    "which no longer recognizes a bare Engine as a SQLAlchemy connectable "
    "and falls through to a legacy DBAPI2 code path that calls "
    "engine.cursor() directly, raising "
    "'Engine' object has no attribute 'cursor'. pull_full() cannot "
    "execute at all against a real database today. Fix tracked as "
    "sub-project E (e.g. pass engine.connect() instead of engine, or pin "
    "pandas<3). This test should start passing once that lands.",
    strict=True,
)
def test_pull_full_returns_all_seeded_rows(pg_schema):
    connector = SQLConnector({"connection_string": pg_schema})
    rows = connector.pull_full("t_customers")
    assert len(rows) == 3
    assert {r["name"] for r in rows} == {"Alice", "Bob", "Carol"}


def test_pull_sample_respects_limit(pg_schema):
    connector = SQLConnector({"connection_string": pg_schema})
    rows = connector.pull_sample("t_customers", limit=2)
    assert len(rows) == 2


def test_pull_delta_with_real_watermark_returns_only_newer_rows(pg_schema):
    connector = SQLConnector({
        "connection_string": pg_schema,
        "watermark_column": "updated_at",
    })
    rows = connector.pull_delta("t_customers", since="2026-01-15 00:00:00")
    assert {r["name"] for r in rows} == {"Bob", "Carol"}


@pytest.mark.xfail(
    reason="Same root cause as test_pull_full_returns_all_seeded_rows: "
    "pull_delta with no watermark_column falls back to pull_full "
    "(sql_connector.py:81-82), which is currently broken against a real "
    "engine under pandas 3.0.3.",
    strict=True,
)
def test_pull_delta_without_watermark_falls_back_to_pull_full(pg_schema):
    connector = SQLConnector({"connection_string": pg_schema})
    rows = connector.pull_delta("t_customers", since=None)
    assert len(rows) == 3

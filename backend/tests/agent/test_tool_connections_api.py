"""P7A: tool provider/connection/version admin API."""
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import quote

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"


def _scoped_url(schema: str) -> str:
    return f"{TEST_DATABASE_URL}?options={quote(f'-csearch_path={schema},public', safe='-=,')}"


def _alembic(schema: str, *args, check=True):
    return subprocess.run(
        [sys.executable, "scripts/run_migrations.py", *args],
        cwd=BACKEND_DIR, env=dict(os.environ, DATABASE_URL=_scoped_url(schema)),
        capture_output=True, text=True, check=check,
    )


@pytest.fixture
def session():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "p7a_conn_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    assert _alembic(schema, "upgrade", "head").returncode == 0
    s = sessionmaker(bind=create_engine(_scoped_url(schema)))()
    s.execute(text(
        "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
        "VALUES ('u-1','a','a@t.com','h','admin',true,:d,now(),now())"
    ), {"d": DEFAULT_DOMAIN})
    s.commit()
    yield s
    s.close()
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def test_create_provider_rejects_unknown_kind(session):
    from app.services.tool_connections import create_provider, ToolConnectionError
    with pytest.raises(ToolConnectionError):
        create_provider(session, actor_id="u-1", name="X", kind="not_a_kind")


def test_create_connection_version_rejects_unknown_search_provider(session):
    from app.services.tool_connections import create_connection, create_provider, create_connection_version, ToolConnectionError
    provider = create_provider(session, actor_id="u-1", name="Web Search", kind="search")
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    with pytest.raises(ToolConnectionError):
        create_connection_version(session, actor_id="u-1", connection_id=connection["id"],
                                  search_provider="not_a_real_provider")


def test_create_connection_version_persists_search_provider(session):
    from app.services.tool_connections import create_connection, create_provider, create_connection_version, list_connection_versions
    provider = create_provider(session, actor_id="u-1", name="Web Search", kind="search")
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    create_connection_version(session, actor_id="u-1", connection_id=connection["id"],
                              endpoint="https://api.bing.microsoft.com/v7.0/search", search_provider="bing")
    versions = list_connection_versions(session, connection_id=connection["id"])
    assert versions[0]["search_provider"] == "bing"


def test_update_connection_version_edits_a_pending_version_in_place(session):
    from app.services.tool_connections import (
        create_connection, create_provider, create_connection_version, update_connection_version,
        list_connection_versions,
    )
    provider = create_provider(session, actor_id="u-1", name="Web Search", kind="search")
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    version = create_connection_version(session, actor_id="u-1", connection_id=connection["id"],
                                        endpoint="https://old.example.com", search_provider="generic")
    update_connection_version(session, actor_id="u-1", version_id=version["id"],
                              endpoint="https://google.serper.dev/search", search_provider="serper")
    versions = list_connection_versions(session, connection_id=connection["id"])
    assert len(versions) == 1  # edited in place, no new version row
    assert versions[0]["endpoint"] == "https://google.serper.dev/search"
    assert versions[0]["search_provider"] == "serper"


def test_update_connection_version_omitted_credential_leaves_existing_one_untouched(session):
    """No read endpoint ever returns `credential_reference` for the edit
    form to prefill, so omitting it on update must not blank out the
    existing key."""
    from app.services.tool_connections import create_connection, create_provider, create_connection_version, update_connection_version
    provider = create_provider(session, actor_id="u-1", name="Web Search", kind="search")
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    version = create_connection_version(session, actor_id="u-1", connection_id=connection["id"],
                                        endpoint="https://x.example.com", credential_reference="secret-key")
    update_connection_version(session, actor_id="u-1", version_id=version["id"], endpoint="https://y.example.com")
    stored = session.execute(text(
        "SELECT credential_reference FROM tool_connection_versions WHERE id = :id"
    ), {"id": version["id"]}).scalar_one()
    assert stored == "secret-key"


def test_update_connection_version_rejects_once_approved(session):
    from app.services.tool_connections import (
        create_connection, create_provider, create_connection_version, approve_connection_version,
        update_connection_version, ToolConnectionError,
    )
    provider = create_provider(session, actor_id="u-1", name="Web Search", kind="search")
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    version = create_connection_version(session, actor_id="u-1", connection_id=connection["id"],
                                        endpoint="https://x.example.com")
    approve_connection_version(session, actor_id="u-1", version_id=version["id"])
    with pytest.raises(ToolConnectionError):
        update_connection_version(session, actor_id="u-1", version_id=version["id"], endpoint="https://y.example.com")


def test_update_connection_version_rejects_unknown_version(session):
    from app.services.tool_connections import update_connection_version, ToolConnectionError
    with pytest.raises(ToolConnectionError):
        update_connection_version(session, actor_id="u-1", version_id="does-not-exist", endpoint="https://y.example.com")


def test_list_connections_surfaces_active_version_target(session):
    """A connection has no name of its own — the admin UI needs the active
    version's search_provider/endpoint to show what it's actually pointed
    at instead of an opaque id."""
    from app.services.tool_connections import (
        activate_connection_version, approve_connection_version, create_connection,
        create_connection_version, create_provider, list_connections,
    )
    provider = create_provider(session, actor_id="u-1", name="Web Search", kind="search")
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    version = create_connection_version(session, actor_id="u-1", connection_id=connection["id"],
                                        endpoint="https://api.bing.microsoft.com/v7.0/search", search_provider="bing")
    approve_connection_version(session, actor_id="u-1", version_id=version["id"])
    activate_connection_version(session, actor_id="u-1", connection_id=connection["id"], version_id=version["id"])
    connections = list_connections(session, provider_id=provider["id"])
    assert connections[0]["active_version_search_provider"] == "bing"
    assert connections[0]["active_version_endpoint"] == "https://api.bing.microsoft.com/v7.0/search"


def test_list_connections_active_version_fields_null_when_not_activated(session):
    from app.services.tool_connections import create_connection, create_provider, list_connections
    provider = create_provider(session, actor_id="u-1", name="Web Search", kind="search")
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    connections = list_connections(session, provider_id=provider["id"])
    assert connections[0]["active_version_search_provider"] is None
    assert connections[0]["active_version_endpoint"] is None


def test_delete_connection_version_removes_a_pending_version(session):
    from app.services.tool_connections import (
        create_connection, create_provider, create_connection_version, delete_connection_version,
        list_connection_versions,
    )
    provider = create_provider(session, actor_id="u-1", name="Web Search", kind="search")
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    version = create_connection_version(session, actor_id="u-1", connection_id=connection["id"])
    delete_connection_version(session, actor_id="u-1", version_id=version["id"])
    assert list_connection_versions(session, connection_id=connection["id"]) == []


def test_delete_connection_version_rejects_once_approved(session):
    from app.services.tool_connections import (
        create_connection, create_provider, create_connection_version, approve_connection_version,
        delete_connection_version, ToolConnectionError,
    )
    provider = create_provider(session, actor_id="u-1", name="Web Search", kind="search")
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    version = create_connection_version(session, actor_id="u-1", connection_id=connection["id"],
                                        endpoint="https://x.example.com")
    approve_connection_version(session, actor_id="u-1", version_id=version["id"])
    with pytest.raises(ToolConnectionError):
        delete_connection_version(session, actor_id="u-1", version_id=version["id"])


def test_delete_connection_version_rejects_unknown_version(session):
    from app.services.tool_connections import delete_connection_version, ToolConnectionError
    with pytest.raises(ToolConnectionError):
        delete_connection_version(session, actor_id="u-1", version_id="does-not-exist")


def test_rename_connection_sets_name(session):
    from app.services.tool_connections import create_connection, create_provider, list_connections, rename_connection
    provider = create_provider(session, actor_id="u-1", name="Web Search", kind="search")
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    result = rename_connection(session, actor_id="u-1", connection_id=connection["id"], name="生产环境-Bing")
    assert result["name"] == "生产环境-Bing"
    connections = list_connections(session, provider_id=provider["id"])
    assert connections[0]["name"] == "生产环境-Bing"


def test_rename_connection_rejects_blank_name(session):
    from app.services.tool_connections import create_connection, create_provider, rename_connection, ToolConnectionError
    provider = create_provider(session, actor_id="u-1", name="Web Search", kind="search")
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    with pytest.raises(ToolConnectionError):
        rename_connection(session, actor_id="u-1", connection_id=connection["id"], name="   ")


def test_rename_connection_rejects_unknown_connection(session):
    from app.services.tool_connections import rename_connection, ToolConnectionError
    with pytest.raises(ToolConnectionError):
        rename_connection(session, actor_id="u-1", connection_id="does-not-exist", name="x")


def test_agent_external_tool_catalog_prefers_connection_name(session):
    from app.services.agent.catalog import agent_external_tool_catalog
    from app.services.tool_connections import (
        activate_connection_version, approve_connection_version, create_connection,
        create_connection_version, create_provider, rename_connection,
    )
    provider = create_provider(session, actor_id="u-1", name="Web Search", kind="search")
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    version = create_connection_version(session, actor_id="u-1", connection_id=connection["id"],
                                        endpoint="https://x.example.com")
    approve_connection_version(session, actor_id="u-1", version_id=version["id"])
    activate_connection_version(session, actor_id="u-1", connection_id=connection["id"], version_id=version["id"])
    rename_connection(session, actor_id="u-1", connection_id=connection["id"], name="生产环境-Bing")
    catalog = agent_external_tool_catalog(session)
    assert catalog[0]["provider_name"] == "生产环境-Bing"


def test_full_provider_connection_version_activation_flow(session):
    from app.services.tool_connections import (
        activate_connection_version, approve_connection_version, create_connection,
        create_connection_version, create_provider,
    )
    provider = create_provider(session, actor_id="u-1", name="Web Search", kind="search")
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    version = create_connection_version(
        session, actor_id="u-1", connection_id=connection["id"],
        endpoint="https://search.example.com/v1", scopes=["search:read"])
    assert version["approval_status"] == "pending"
    approve_connection_version(session, actor_id="u-1", version_id=version["id"])
    activated = activate_connection_version(
        session, actor_id="u-1", connection_id=connection["id"], version_id=version["id"])
    assert activated["active_version_id"] == version["id"]


def test_activate_rejects_unapproved_version(session):
    from app.services.tool_connections import (
        create_connection, create_connection_version, create_provider, activate_connection_version,
        ToolConnectionError,
    )
    provider = create_provider(session, actor_id="u-1", name="Web Search", kind="search")
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    version = create_connection_version(session, actor_id="u-1", connection_id=connection["id"])
    with pytest.raises(ToolConnectionError):
        activate_connection_version(
            session, actor_id="u-1", connection_id=connection["id"], version_id=version["id"])


def test_test_endpoint_search_healthy(session, monkeypatch):
    from app.services.tool_connections import (
        approve_connection_version, create_connection, create_connection_version,
        create_provider, test_connection_version,
    )

    def _fake_web_search(*, endpoint, api_key, query, result_limit=5, timeout_seconds=10.0, provider=None):
        return []

    monkeypatch.setattr("app.services.tools.search.web_search", _fake_web_search)
    provider = create_provider(session, actor_id="u-1", name="Web Search", kind="search")
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    version = create_connection_version(session, actor_id="u-1", connection_id=connection["id"],
                                        endpoint="https://search.example.com/v1")
    approve_connection_version(session, actor_id="u-1", version_id=version["id"])
    result = test_connection_version(session, version_id=version["id"])
    assert result["status"] == "healthy"
    # the verdict is persisted so list views reflect probe results
    persisted = session.execute(text(
        "SELECT health_status FROM tool_connection_versions WHERE id = :id"
    ), {"id": version["id"]}).scalar_one()
    assert persisted == "healthy"


def test_create_provider_accepts_browser_use_kind(session):
    from app.services.tool_connections import create_provider
    provider = create_provider(session, actor_id="u-1", name="Browser Agent", kind="browser_use")
    assert provider["kind"] == "browser_use"


def test_test_endpoint_browser_use_healthy(session, monkeypatch):
    from app.services.tool_connections import (
        approve_connection_version, create_connection, create_connection_version,
        create_provider, test_connection_version,
    )

    def _fake_run_browser_task(*, endpoint, api_key, task, timeout_seconds=60.0):
        return {"content": "ok", "url": endpoint, "artifact": None}

    monkeypatch.setattr("app.services.tools.browser_use.run_browser_task", _fake_run_browser_task)
    provider = create_provider(session, actor_id="u-1", name="Browser Agent", kind="browser_use")
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    version = create_connection_version(session, actor_id="u-1", connection_id=connection["id"],
                                        endpoint="https://browser-use.example.com/run")
    approve_connection_version(session, actor_id="u-1", version_id=version["id"])
    result = test_connection_version(session, version_id=version["id"])
    assert result["status"] == "healthy"


def test_test_endpoint_browser_use_upstream_error_unhealthy(session, monkeypatch):
    from app.services.tool_connections import (
        approve_connection_version, create_connection, create_connection_version,
        create_provider, test_connection_version,
    )

    def _fake_run_browser_task(*, endpoint, api_key, task, timeout_seconds=60.0):
        from app.services.tools.browser_use import BrowserUseError
        raise BrowserUseError("BROWSER_USE_UPSTREAM_ERROR:500")

    monkeypatch.setattr("app.services.tools.browser_use.run_browser_task", _fake_run_browser_task)
    provider = create_provider(session, actor_id="u-1", name="Browser Agent", kind="browser_use")
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    version = create_connection_version(session, actor_id="u-1", connection_id=connection["id"],
                                        endpoint="https://browser-use.example.com/run")
    approve_connection_version(session, actor_id="u-1", version_id=version["id"])
    result = test_connection_version(session, version_id=version["id"])
    assert result["status"] == "unhealthy"


def test_test_endpoint_unapproved_version_rejected(session):
    from app.services.tool_connections import (
        create_connection, create_connection_version, create_provider,
        test_connection_version, ToolConnectionError,
    )
    provider = create_provider(session, actor_id="u-1", name="Web Search", kind="search")
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    version = create_connection_version(session, actor_id="u-1", connection_id=connection["id"])
    with pytest.raises(ToolConnectionError):
        test_connection_version(session, version_id=version["id"])


def test_test_endpoint_playwright_missing_allowlist_unhealthy(session):
    from app.services.tool_connections import (
        approve_connection_version, create_connection, create_connection_version,
        create_provider, test_connection_version,
    )
    provider = create_provider(session, actor_id="u-1", name="Web Render", kind="playwright")
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    version = create_connection_version(session, actor_id="u-1", connection_id=connection["id"])
    approve_connection_version(session, actor_id="u-1", version_id=version["id"])
    result = test_connection_version(session, version_id=version["id"])
    assert result["status"] == "unhealthy"
    assert "ALLOWLIST" in result["detail"]


def test_test_endpoint_search_connection_error_unhealthy(session, monkeypatch):
    import httpx
    from app.services.tool_connections import (
        approve_connection_version, create_connection, create_connection_version,
        create_provider, test_connection_version,
    )

    def _failing_web_search(*, endpoint, api_key, query, result_limit=5, timeout_seconds=10.0, provider=None):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr("app.services.tools.search.web_search", _failing_web_search)
    provider = create_provider(session, actor_id="u-1", name="Web Search", kind="search")
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    version = create_connection_version(session, actor_id="u-1", connection_id=connection["id"],
                                        endpoint="https://search.example.com/v1")
    approve_connection_version(session, actor_id="u-1", version_id=version["id"])
    result = test_connection_version(session, version_id=version["id"])
    assert result == {"status": "unhealthy", "detail": "boom"}


def test_test_endpoint_search_non_dict_json_unhealthy(session, monkeypatch):
    """The probe's "never an exception" contract holds even when the provider
    returns a JSON array/string (web_search raises AttributeError on
    body.get — the probe must surface it as structured unhealthy, not 500)."""
    from app.services.tool_connections import (
        approve_connection_version, create_connection, create_connection_version,
        create_provider, test_connection_version,
    )

    def _non_dict_web_search(*, endpoint, api_key, query, result_limit=5, timeout_seconds=10.0, provider=None):
        raise AttributeError("'list' object has no attribute 'get'")

    monkeypatch.setattr("app.services.tools.search.web_search", _non_dict_web_search)
    provider = create_provider(session, actor_id="u-1", name="Web Search", kind="search")
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    version = create_connection_version(session, actor_id="u-1", connection_id=connection["id"],
                                        endpoint="https://search.example.com/v1")
    approve_connection_version(session, actor_id="u-1", version_id=version["id"])
    result = test_connection_version(session, version_id=version["id"])
    assert result["status"] == "unhealthy"
    assert "'list' object has no attribute 'get'" in result["detail"]


def test_test_endpoint_playwright_navigation_failure_unhealthy(session, monkeypatch):
    from app.services.tools.playwright import PlaywrightError
    from app.services.tool_connections import (
        approve_connection_version, create_connection, create_connection_version,
        create_provider, test_connection_version,
    )

    def _failing_browse_page(*, url, allowed_domains, timeout_seconds=20.0, max_bytes=200_000):
        raise PlaywrightError("PLAYWRIGHT_NAVIGATION_FAILED:Error:executable missing")

    monkeypatch.setattr("app.services.tools.playwright.browse_page", _failing_browse_page)
    provider = create_provider(session, actor_id="u-1", name="Web Render", kind="playwright")
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    version = create_connection_version(session, actor_id="u-1", connection_id=connection["id"],
                                        allowlists={"domains": ["example.com"]})
    approve_connection_version(session, actor_id="u-1", version_id=version["id"])
    result = test_connection_version(session, version_id=version["id"])
    assert result["status"] == "unhealthy"
    assert "NAVIGATION_FAILED" in result["detail"]


def test_test_endpoint_playwright_allowlist_defect_unhealthy(session, monkeypatch):
    """A guard-blocked probe (URL_BLOCKED / DOMAIN_NOT_ALLOWED) proves nothing
    about browser engagement — the stored allowlist is a config defect and
    every agent call would fail closed forever, so the probe is unhealthy."""
    from app.services.tools.playwright import PlaywrightError
    from app.services.tool_connections import (
        approve_connection_version, create_connection, create_connection_version,
        create_provider, test_connection_version,
    )

    def _blocked_browse_page(*, url, allowed_domains, timeout_seconds=20.0, max_bytes=200_000):
        raise PlaywrightError("PLAYWRIGHT_DOMAIN_NOT_ALLOWED")

    monkeypatch.setattr("app.services.tools.playwright.browse_page", _blocked_browse_page)
    provider = create_provider(session, actor_id="u-1", name="Web Render", kind="playwright")
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    version = create_connection_version(session, actor_id="u-1", connection_id=connection["id"],
                                        allowlists={"domains": ["127.0.0.1"]})
    approve_connection_version(session, actor_id="u-1", version_id=version["id"])
    result = test_connection_version(session, version_id=version["id"])
    assert result["status"] == "unhealthy"
    assert "DOMAIN_NOT_ALLOWED" in result["detail"]


def test_list_connection_versions_returns_all_versions_newest_first(session):
    from app.services.tool_connections import (
        create_connection, create_connection_version, create_provider, list_connection_versions,
    )
    provider = create_provider(session, actor_id="u-1", name="Web Search", kind="search")
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    v1 = create_connection_version(session, actor_id="u-1", connection_id=connection["id"], endpoint="https://a")
    v2 = create_connection_version(session, actor_id="u-1", connection_id=connection["id"], endpoint="https://b")
    versions = list_connection_versions(session, connection_id=connection["id"])
    assert [v["id"] for v in versions] == [v2["id"], v1["id"]]
    assert versions[0]["endpoint"] == "https://b"
    assert versions[0]["approval_status"] == "pending"
    assert versions[0]["health_status"] == "unknown"


def test_list_connection_versions_empty_for_unknown_connection(session):
    from app.services.tool_connections import list_connection_versions
    assert list_connection_versions(session, connection_id="nonexistent") == []


def test_seed_default_tool_connections_activates_playwright_and_scaffolds_search(session):
    from app.services.tool_connections import seed_default_tool_connections, list_providers, list_connections

    seed_default_tool_connections(session, actor_id="u-1")

    providers = {p["kind"]: p for p in list_providers(session)}
    assert set(providers) == {"playwright", "search"}

    pw_conn = list_connections(session, provider_id=providers["playwright"]["id"])[0]
    assert pw_conn["active_version_id"] is not None
    pw_version = session.execute(text(
        "SELECT approval_status, allowlists FROM tool_connection_versions WHERE id = :id"
    ), {"id": pw_conn["active_version_id"]}).mappings().one()
    assert pw_version["approval_status"] == "approved"
    assert "wikipedia.org" in pw_version["allowlists"]["domains"]

    search_conn = list_connections(session, provider_id=providers["search"]["id"])[0]
    assert search_conn["active_version_id"] is None


def test_seed_default_tool_connections_is_idempotent(session):
    from app.services.tool_connections import seed_default_tool_connections, list_providers

    seed_default_tool_connections(session, actor_id="u-1")
    seed_default_tool_connections(session, actor_id="u-1")

    kinds = [p["kind"] for p in list_providers(session)]
    assert kinds.count("playwright") == 1
    assert kinds.count("search") == 1


def test_seed_default_tool_connections_skips_kind_with_existing_provider(session):
    from app.services.tool_connections import create_provider, seed_default_tool_connections, list_providers

    create_provider(session, actor_id="u-1", name="Custom Search", kind="search")
    seed_default_tool_connections(session, actor_id="u-1")

    search_providers = [p for p in list_providers(session) if p["kind"] == "search"]
    assert len(search_providers) == 1
    assert search_providers[0]["name"] == "Custom Search"

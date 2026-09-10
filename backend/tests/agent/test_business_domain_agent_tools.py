"""供应链/信贷/财务 Agent 的 网页查询(search)/Playwright 工具可用性验证.

Fold 到 Agent 工作区的 Tool Connections 为每个业务域 Agent 提供两个内置工具
(见 app/services/tool_connections.py:seed_default_tool_connections)；这里对每个
域各建一个 Agent version，绑定两个工具后经 ToolGateway 完整分派一次，证明
绑定 + Gateway 分派 + adapter 包装的链路对三个业务域都是正确、可用的。

网络层按仓库既有约定 mock（同 test_tool_gateway_external.py）：Playwright 的
真实浏览器/DNS 出站在沙箱环境下会被 SSRF 守卫合法拦截（沙箱把外部域名解析到
IANA 保留基准测试网段 198.18.0.0/15），网页查询 也未配置真实外部搜索 API —
两者都不是这里要验证的东西；这里验证的是 Ontexus 自己拥有的分派/绑定/清洗
逻辑对三个业务域都正确无误，一旦有真实 endpoint/真实网络，链路直接可用。
"""
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

DOMAINS = ["供应链", "信贷", "财务"]


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
    schema = "biz_domain_tools_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    assert _alembic(schema, "upgrade", "0015_external_mcp").returncode == 0
    s = sessionmaker(bind=create_engine(_scoped_url(schema)))()
    s.execute(text(
        "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
        "VALUES ('u-1','a','a@t.com','h','admin',true,:d,now(),now())"
    ), {"d": DEFAULT_DOMAIN})
    s.execute(text(
        "INSERT INTO model_configs (id,name,config_type,api_base,api_key_encrypted,provider,models,options,created_by,created_at,updated_at) "
        "VALUES ('mc-1','m','llm',NULL,'','openai','[]'::json,'{}'::json,'u-1',now(),now())"
    ))
    s.execute(text(
        "INSERT INTO model_config_versions (id, model_config_id, version_no, provider, options, behavior_hash, model_contract, created_at) "
        "VALUES ('mcv-1', 'mc-1', 1, 'openai', '{}'::json, :hash, '[]'::json, now())"
    ), {"hash": "0" * 64})
    app_schema_version_id = s.execute(text(
        "SELECT active_version_id FROM application_state_schema_registries WHERE application_key = 'chat-v1'"
    )).scalar_one()
    # one Agent (+ version + run grant) per business domain named in the goal
    for domain in DOMAINS:
        agent_id = f"ag-{domain}"
        version_id = f"av-{domain}"
        s.execute(text(
            "INSERT INTO agents (id,visibility,status,owner_id,created_at,updated_at) "
            "VALUES (:id,'private','active','u-1',now(),now())"
        ), {"id": agent_id})
        s.execute(text(
            "INSERT INTO agent_versions (id, agent_id, version_no, name, default_model_config_version_id, "
            "default_model_name, system_prompt, application_state_schema_version_id, config_hash, created_by, created_at) "
            "VALUES (:vid, :aid, 1, :name, 'mcv-1', 'test-model', '', :svid, 'h', 'u-1', now())"
        ), {"vid": version_id, "aid": agent_id, "name": f"{domain}-Agent", "svid": app_schema_version_id})
        s.execute(text(
            "INSERT INTO agent_access_grants (id, agent_id, user_id, capabilities, revision, status, "
            "created_by, created_at, updated_at) "
            "VALUES (:gid, :aid, 'u-1', '[\"run\"]'::json, 1, 'active', 'u-1', now(), now())"
        ), {"gid": f"grant-{domain}", "aid": agent_id})
    s.commit()
    yield s
    s.close()
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def _bind_search(session, agent_version_id: str) -> str:
    from app.services.agent.configuration import bind_external_tool
    from app.services.tool_connections import (
        approve_connection_version, create_connection, create_connection_version, create_provider,
    )
    provider = create_provider(session, actor_id="u-1", name="网页查询", kind="search")
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    version = create_connection_version(session, actor_id="u-1", connection_id=connection["id"],
                                        endpoint="https://search.example.com/v1")
    approve_connection_version(session, actor_id="u-1", version_id=version["id"])
    bind_external_tool(session, actor_id="u-1", agent_version_id=agent_version_id,
                       tool_connection_version_id=version["id"], alias="web_search")
    return version["id"]


def _bind_playwright(session, agent_version_id: str) -> str:
    from app.services.agent.configuration import bind_external_tool
    from app.services.tool_connections import (
        approve_connection_version, create_connection, create_connection_version, create_provider,
    )
    provider = create_provider(session, actor_id="u-1", name="Playwright", kind="playwright")
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    version = create_connection_version(session, actor_id="u-1", connection_id=connection["id"],
                                        allowlists={"domains": ["example.com"]})
    approve_connection_version(session, actor_id="u-1", version_id=version["id"])
    bind_external_tool(session, actor_id="u-1", agent_version_id=agent_version_id,
                       tool_connection_version_id=version["id"], alias="browse_page")
    return version["id"]


@pytest.mark.parametrize("domain", DOMAINS)
def test_search_tool_usable_for_domain_agent(session, monkeypatch, domain):
    from app.services.tool_gateway import GatewayRequest, ToolGateway

    def _fake_web_search(*, endpoint, api_key, query, result_limit=5, timeout_seconds=10.0):
        from app.services.untrusted_artifact import make_artifact
        return [{"title": f"{domain} 搜索结果", "url": "https://x.example.com",
                 "artifact": make_artifact(source="https://x.example.com", media_type="text/plain",
                                           raw_content=f"{domain} 相关内容")}]

    monkeypatch.setattr("app.services.tools.search.web_search", _fake_web_search)
    agent_id, version_id = f"ag-{domain}", f"av-{domain}"
    connection_version_id = _bind_search(session, version_id)
    gateway = ToolGateway(session)
    result = gateway.execute(GatewayRequest(
        agent_id=agent_id, user_id="u-1", descriptor_id="external.search", operation="external_tool_call",
        parameters={"agent_version_id": version_id, "tool_connection_version_id": connection_version_id,
                    "query": f"{domain} 行业动态"},
    ))
    assert result.outcome == "untrusted_read"
    assert result.payload["results"][0]["content"] == f"{domain} 相关内容"


@pytest.mark.parametrize("domain", DOMAINS)
def test_playwright_tool_usable_for_domain_agent(session, monkeypatch, domain):
    from app.services.tool_gateway import GatewayRequest, ToolGateway

    def _fake_browse_page(*, url, allowed_domains, timeout_seconds=20.0, max_bytes=200_000):
        from app.services.untrusted_artifact import make_artifact
        return {"title": f"{domain} 参考页面", "final_url": "https://docs.example.com/",
                "artifact": make_artifact(source="https://docs.example.com/", media_type="text/plain",
                                          raw_content=f"{domain} 页面正文")}

    monkeypatch.setattr("app.services.tools.playwright.browse_page", _fake_browse_page)
    agent_id, version_id = f"ag-{domain}", f"av-{domain}"
    connection_version_id = _bind_playwright(session, version_id)
    gateway = ToolGateway(session)
    result = gateway.execute(GatewayRequest(
        agent_id=agent_id, user_id="u-1", descriptor_id="external.playwright", operation="external_tool_call",
        parameters={"agent_version_id": version_id, "tool_connection_version_id": connection_version_id,
                    "url": "https://docs.example.com/"},
    ))
    assert result.outcome == "untrusted_read"
    assert result.payload["results"][0]["content"] == f"{domain} 页面正文"


@pytest.mark.parametrize("domain", DOMAINS)
def test_both_tools_bind_independently_on_same_domain_agent(session, monkeypatch, domain):
    """A domain Agent needs both tools live at once (per the Tool Connections
    defaults) — binding one must not disturb the other's alias/dispatch."""
    from app.services.tool_gateway import GatewayRequest, ToolGateway

    monkeypatch.setattr(
        "app.services.tools.search.web_search",
        lambda **k: [{"title": "t", "url": "https://x.example.com",
                     "artifact": __import__("app.services.untrusted_artifact", fromlist=["make_artifact"])
                     .make_artifact(source="https://x.example.com", media_type="text/plain", raw_content="s")}],
    )
    monkeypatch.setattr(
        "app.services.tools.playwright.browse_page",
        lambda **k: {"title": "t", "final_url": "https://docs.example.com/",
                    "artifact": __import__("app.services.untrusted_artifact", fromlist=["make_artifact"])
                    .make_artifact(source="https://docs.example.com/", media_type="text/plain", raw_content="p")},
    )
    version_id = f"av-{domain}"
    search_cv = _bind_search(session, version_id)
    playwright_cv = _bind_playwright(session, version_id)
    gateway = ToolGateway(session)

    search_result = gateway.execute(GatewayRequest(
        agent_id=f"ag-{domain}", user_id="u-1", descriptor_id="external.search", operation="external_tool_call",
        parameters={"agent_version_id": version_id, "tool_connection_version_id": search_cv, "query": "q"},
    ))
    playwright_result = gateway.execute(GatewayRequest(
        agent_id=f"ag-{domain}", user_id="u-1", descriptor_id="external.playwright", operation="external_tool_call",
        parameters={"agent_version_id": version_id, "tool_connection_version_id": playwright_cv,
                    "url": "https://docs.example.com/"},
    ))
    assert search_result.outcome == "untrusted_read"
    assert playwright_result.outcome == "untrusted_read"

"""P4A-GATEWAY: governed Ontology tool gateway.

ToolGateway.execute: descriptor/schema lookup, trust classification, policy
operation-map, current-grant + data-grant recheck, trusted read dispatch with
SQL refetch, and audit trace.  No adapter bypasses it and no instance writes
execute.  Every read path is mapped to a data capability.
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


def test_p4a_gateway_red_contract():
    failures = []
    for path in ("app/services/tool_gateway.py", "app/services/ontology_tools.py"):
        p = BACKEND_DIR / path
        if not p.exists():
            failures.append(f"missing {path}")
    gw = BACKEND_DIR / "app" / "services" / "tool_gateway.py"
    if gw.exists():
        for symbol in ("ToolGateway", "execute", "TRUSTED_READ_DESCRIPTORS"):
            if symbol not in gw.read_text():
                failures.append(f"tool_gateway.py missing {symbol}")
    if failures:
        pytest.fail("RED_P4A_GATEWAY: " + "; ".join(failures))


def _scoped_url(schema: str) -> str:
    return f"{TEST_DATABASE_URL}?options={quote(f'-csearch_path={schema},public', safe='-=,')}"


def _alembic(schema: str, *args, check=True):
    return subprocess.run(
        [sys.executable, "scripts/run_migrations.py", *args],
        cwd=BACKEND_DIR,
        env=dict(os.environ, DATABASE_URL=_scoped_url(schema)),
        capture_output=True,
        text=True,
        check=check,
    )


@pytest.fixture
def schema():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "p4a_gateway_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    assert _alembic(schema, "upgrade", "0006_agent_runtime").returncode == 0
    yield schema
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def _session(schema):
    return sessionmaker(bind=create_engine(_scoped_url(schema)))()


def _seed(schema):
    from app.services.publication.compiler import compile_ontology_release
    s = _session(schema)
    s.execute(text(
        "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
        "VALUES ('u-1','s','s@t.com','h','editor',true,:d,now(),now())"
    ), {"d": DEFAULT_DOMAIN})
    s.execute(text(
        "INSERT INTO ontology_projects (id,name,domain,version,status,created_by,created_at,updated_at,security_domain_id,working_revision) "
        "VALUES ('o-1','O','test','v1','published','u-1',now(),now(),:d,1)"
    ), {"d": DEFAULT_DOMAIN})
    s.execute(text(
        "INSERT INTO entities (id,ontology_id,name_cn,name_en,properties,confidence,version,created_at,updated_at) "
        "VALUES ('e-1','o-1','实体','E','{}'::json,0.9,'v1',now(),now()),"
        "('e-2','o-1','实体2','E2','{}'::json,0.9,'v1',now(),now())"
    ))
    s.commit()
    release = compile_ontology_release(s, ontology_id="o-1", actor_id="u-1")
    s.execute(text(
        "INSERT INTO relations (id, ontology_id, type, source_entity, target_entity, properties, confidence, created_at) "
        "VALUES ('r-1', 'o-1', 'related', 'e-1', 'e-2', '{}'::json, 0.9, now())"
    ))
    s.commit()
    release2 = compile_ontology_release(s, ontology_id="o-1", actor_id="u-1")
    s.execute(text(
        "INSERT INTO agents (id,visibility,status,owner_id,created_at,updated_at) "
        "VALUES ('a-1','private','active','u-1',now(),now())"
    ))
    s.execute(text(
        "INSERT INTO agent_access_grants (id, agent_id, user_id, capabilities, revision, status, created_by, created_at, updated_at) "
        "VALUES (:id, 'a-1', 'u-1', CAST(:caps AS json), 1, 'active', 'u-1', now(), now())"
    ), {"id": str(uuid.uuid4()), "caps": '["discover","run","view_config","edit","view_audit"]'})
    s.execute(text(
        "INSERT INTO entity_instances (id, entity_id, ontology_id, row_identity, row_data, created_at) "
        "VALUES ('i-1', 'e-1', 'o-1', 'a', '{\"name\":\"Alpha\"}'::json, now()),"
        "('i-2', 'e-2', 'o-1', 'b', '{\"name\":\"Beta\"}'::json, now())"
    ))
    s.execute(text(
        "INSERT INTO entity_instance_relations (id, ontology_id, source_instance_id, target_instance_id, "
        "relation_definition_id, properties, revision, created_at, updated_at) "
        "VALUES ('ir-1', 'o-1', 'i-1', 'i-2', 'r-1', '{}'::json, 1, now(), now())"
    ))
    s.execute(text(
        "INSERT INTO ontology_data_grants (id, ontology_id, user_id, capabilities, status, revision, created_by) "
        "VALUES (:id, 'o-1', 'u-1', CAST(:caps AS json), 'active', 1, 'u-1')"
    ), {"id": str(uuid.uuid4()), "caps": '["read_instances","traverse_relations","discover"]'})
    s.commit()
    s.close()
    return release2["release_id"]


def test_gateway_read_instances_mapped_and_traced(schema):
    release_id = _seed(schema)
    s = _session(schema)
    from app.services.tool_gateway import ToolGateway, GatewayRequest
    gw = ToolGateway(s)
    result = gw.execute(
        GatewayRequest(agent_id="a-1", user_id="u-1",
                       descriptor_id="ontology.read_instances", operation="read",
                       parameters={"ontology_id": "o-1", "release_id": release_id, "query": "Alpha"}),
        ontology_id="o-1",
    )
    assert result.outcome == "read"
    assert result.payload["items"][0]["instance_id"] == "i-1"
    assert result.correlation_id.startswith("gateway:ontology.read_instances:")
    assert any(t["grant_recheck"] == "passed" for t in result.trace)
    s.close()


def test_gateway_traverse_relations(schema):
    release_id = _seed(schema)
    s = _session(schema)
    from app.services.tool_gateway import ToolGateway, GatewayRequest
    gw = ToolGateway(s)
    result = gw.execute(
        GatewayRequest(agent_id="a-1", user_id="u-1",
                       descriptor_id="ontology.traverse_relations", operation="read",
                       parameters={"ontology_id": "o-1", "release_id": release_id, "instance_id": "i-1"}),
        ontology_id="o-1",
    )
    assert result.outcome == "read"
    assert len(result.payload["edges"]) == 1
    assert result.payload["edges"][0]["edge_id"] == "ir-1"
    s.close()


def test_gateway_unknown_descriptor_fails_closed(schema):
    _seed(schema)
    s = _session(schema)
    from app.services.tool_gateway import ToolGateway, GatewayRequest, ToolGatewayError
    gw = ToolGateway(s)
    with pytest.raises(ToolGatewayError, match="DESCRIPTOR_UNKNOWN"):
        gw.execute(GatewayRequest(agent_id="a-1", user_id="u-1",
                                  descriptor_id="external.search", operation="read", parameters={}))
    s.close()


def test_gateway_revoked_grant_rejected(schema):
    release_id = _seed(schema)
    s = _session(schema)
    from app.services.tool_gateway import ToolGateway, GatewayRequest, ToolGatewayError
    s.execute(text("UPDATE ontology_data_grants SET status = 'revoked' WHERE user_id = 'u-1'"))
    s.commit()
    gw = ToolGateway(s)
    with pytest.raises(ToolGatewayError, match="DATA_GRANT_REVOKED"):
        gw.execute(
            GatewayRequest(agent_id="a-1", user_id="u-1",
                           descriptor_id="ontology.read_instances", operation="read",
                           parameters={"ontology_id": "o-1", "release_id": release_id, "query": "Alpha"}),
            ontology_id="o-1",
        )
    s.close()


def test_operation_map_covers_read_paths(schema):
    _seed(schema)
    from app.services.tool_gateway import TRUSTED_READ_DESCRIPTORS
    from app.services.agent.policy import operation_capabilities
    # every trusted read descriptor's capability is registered on some runtime
    # operation in the single code mapping (Section 8 operation-map evidence)
    mapped_all = set()
    for path in ("/api/v1/ontologies/{ontology_id}/entities/{entity_id}/instances",
                 "/api/v1/ontologies/{ontology_id}/graph",
                 "/api/v1/ontologies/{ontology_id}/logic"):
        mapped_all |= operation_capabilities("GET", path)
    # the action-preview descriptor rides the instance-action vocabulary
    # registered on the run route
    mapped_all |= operation_capabilities(
        "POST", "/api/v2/ontologies/{ontology_id}/actions/{action_id}/run")
    for caps in TRUSTED_READ_DESCRIPTORS.values():
        assert caps <= mapped_all

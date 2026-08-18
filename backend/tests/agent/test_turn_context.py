"""P4A-CONTEXT: pinned Agent context assembly.

Latest-once resolution of the release/model/retrieval/tool tuple with
budgets, SQL-refetched authorized reads and citations.  The current Turn
keeps its pinned release stable across mid-publication; the next Turn
refreshes to the latest published release.  No graph/stream/write Action.
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


def test_p4a_context_red_contract():
    failures = []
    for path in ("app/services/runtime/context.py", "app/services/ontology_query.py"):
        p = BACKEND_DIR / path
        if not p.exists():
            failures.append(f"missing {path}")
    svc = BACKEND_DIR / "app" / "services" / "runtime" / "context.py"
    if svc.exists():
        for symbol in ("resolve_pinned_context", "search_authorized", "PinnedContext"):
            if symbol not in svc.read_text():
                failures.append(f"context.py missing {symbol}")
    if failures:
        pytest.fail("RED_P4A_CONTEXT: " + "; ".join(failures))


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
    schema = "p4a_context_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    assert _alembic(schema, "upgrade", "0014_signed_skills").returncode == 0
    yield schema
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def _session(schema):
    return sessionmaker(bind=create_engine(_scoped_url(schema)))()


def _seed(schema, *, editor_id="u-1", agent_id="a-1", ontology_id="o-1",
          with_release=False, with_binding=False, turn_ids=("t-1",)):
    from app.services.publication.compiler import compile_ontology_release
    s = _session(schema)
    s.execute(text(
        "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
        "VALUES (:u,'s','s@t.com','h','editor',true,:d,now(),now())"
    ), {"u": editor_id, "d": DEFAULT_DOMAIN})
    s.execute(text(
        "INSERT INTO ontology_projects (id,name,domain,version,status,created_by,created_at,updated_at,security_domain_id,working_revision) "
        "VALUES (:o,'O','test','v1',:status,:u,now(),now(),:d,1)"
    ), {"o": ontology_id, "u": editor_id, "d": DEFAULT_DOMAIN, "status": "published" if with_release else "created"})
    s.execute(text(
        "INSERT INTO entities (id,ontology_id,name_cn,name_en,properties,confidence,version,created_at,updated_at) "
        "VALUES ('e-1',:o,'实体','E','{}'::json,0.9,'v1',now(),now())"
    ), {"o": ontology_id})
    s.commit()
    release_id = None
    if with_release:
        release = compile_ontology_release(s, ontology_id=ontology_id, actor_id=editor_id)
        release_id = release["release_id"]
    s.execute(text(
        "INSERT INTO agents (id,visibility,status,owner_id,created_at,updated_at) "
        "VALUES (:id,'private','active',:u,now(),now())"
    ), {"id": agent_id, "u": editor_id})
    s.execute(text(
        "INSERT INTO agent_access_grants (id, agent_id, user_id, capabilities, revision, status, created_by, created_at, updated_at) "
        "VALUES (:id, :agent, :u, CAST(:caps AS json), 1, 'active', :u, now(), now())"
    ), {"id": str(uuid.uuid4()), "agent": agent_id, "u": editor_id,
        "caps": '["discover", "run", "view_config", "edit", "view_audit"]'})
    s.execute(text(
        "INSERT INTO model_configs (id,name,config_type,api_base,api_key_encrypted,provider,models,options,created_by,created_at,updated_at) "
        "VALUES ('m-1','m','llm',NULL,'','openai','[]'::json,'{}'::json,:owner,now(),now())"
    ), {"owner": editor_id})
    s.execute(text(
        "INSERT INTO model_config_versions (id, model_config_id, version_no, provider, options, behavior_hash, model_contract, created_at) "
        "VALUES ('mv-1','m-1',1,'openai','{}'::json,:hash,'[]'::json,now())"
    ), {"hash": "0" * 64})
    s.execute(text("UPDATE model_configs SET active_version_id = 'mv-1' WHERE id = 'm-1'"))
    s.execute(text(
        "INSERT INTO agent_versions (id, agent_id, version_no, name, default_model_config_version_id, "
        "default_model_name, system_prompt, memory_settings, application_state_schema_version_id, "
        "config_hash, created_by, created_at) "
        "VALUES ('v-1', :agent, 1, 'A', 'mv-1', 'gpt-4o', 'p', '{}'::json, "
        "(SELECT v.id FROM application_state_schema_versions v "
        "JOIN application_state_schema_registries r ON r.active_version_id = v.id "
        "WHERE r.application_key = 'chat-v1'), :hash, :u, now())"
    ), {"agent": agent_id, "hash": "a" * 64, "u": editor_id})
    s.execute(text("UPDATE agents SET active_version_id = 'v-1' WHERE id = :agent"), {"agent": agent_id})
    if with_binding:
        s.execute(text(
            "INSERT INTO agent_ontology_bindings (id, agent_version_id, ontology_id, capabilities, allowlists) "
            "VALUES (:id, 'v-1', :o, CAST(:caps AS json), CAST(:al AS json))"
        ), {"id": str(uuid.uuid4()), "o": ontology_id, "caps": '["discover","read_schema","read_instances"]',
            "al": '{"entities": ["e-1"]}'})
        s.execute(text(
            "INSERT INTO agent_retrieval_sources (id, agent_version_id, source_id, revision, kind, config_hash, applicability_hash) "
            "VALUES (:id, 'v-1', 'src-1', 1, 'fixed', :ch, :ah)"
        ), {"id": str(uuid.uuid4()), "ch": "b" * 64, "ah": "c" * 64})
    for i, tid in enumerate(turn_ids):
        s.execute(text(
            "INSERT INTO agent_sessions (id, agent_id, owner_user_id, status) "
            "VALUES (:sid, :agent, :u, 'active')"
        ), {"sid": f"s-{i+1}", "agent": agent_id, "u": editor_id})
        s.execute(text(
            "INSERT INTO agent_turns (id, session_id, status, created_at, updated_at) "
            "VALUES (:tid, :sid, 'queued', now(), now())"
        ), {"tid": tid, "sid": f"s-{i+1}"})
    s.commit()
    s.close()
    return release_id


def test_resolve_pinned_context_pins_release_model_tools(schema):
    release_id = _seed(schema, with_release=True, with_binding=True)
    s = _session(schema)
    from app.services.runtime.context import resolve_pinned_context
    ctx = resolve_pinned_context(s, turn_id="t-1", session_id="s-1")
    assert ctx.turn_id == "t-1"
    assert ctx.release_id == release_id
    assert ctx.release_version_no == 1
    assert ctx.model_config_version_id == "mv-1"
    assert ctx.model_name == "gpt-4o"
    assert ctx.ontology_ids == ("o-1",)
    assert len(ctx.retrieval_sources) == 1
    assert ctx.retrieval_sources[0]["source_id"] == "src-1"
    assert ctx.budgets["messages"] == 12
    assert any(c["type"] == "release" for c in ctx.citations)
    s.close()


def test_current_turn_stable_after_mid_publication(schema):
    """The first Turn's pinned release stays stable even after a new release
    is published mid-turn; a fresh Turn resolves the latest release."""
    _seed(schema, with_release=True, with_binding=True, turn_ids=("t-old", "t-new"))
    s = _session(schema)
    from app.services.runtime.context import resolve_pinned_context
    from app.services.publication.compiler import compile_ontology_release
    old_ctx = resolve_pinned_context(s, turn_id="t-old", session_id="s-1")
    # add an entity (schema change) and publish v2 mid-turn
    s.execute(text(
        "INSERT INTO entities (id,ontology_id,name_cn,name_en,properties,confidence,version,created_at,updated_at) "
        "VALUES ('e-2','o-1','实体2','E2','{}'::json,0.9,'v1',now(),now())"
    ))
    s.commit()
    release2 = compile_ontology_release(s, ontology_id="o-1", actor_id="u-1")
    assert release2["version_no"] == 2
    new_ctx = resolve_pinned_context(s, turn_id="t-new", session_id="s-2")
    # current turn stable, next turn refreshes
    assert old_ctx.release_version_no == 1
    assert new_ctx.release_version_no == 2
    assert new_ctx.release_id == release2["release_id"]
    s.close()


def test_search_authorized_refetch_and_revoke(schema):
    release_id = _seed(schema, with_release=True, with_binding=True)
    s = _session(schema)
    s.execute(text(
        "INSERT INTO entity_instances (id, entity_id, ontology_id, row_identity, row_data, created_at) "
        "VALUES ('i-1', 'e-1', 'o-1', 'a', '{\"name\":\"Alpha\"}'::json, now())"
    ))
    s.execute(text(
        "INSERT INTO ontology_data_grants (id, ontology_id, user_id, capabilities, status, revision, created_by) "
        "VALUES (:id, 'o-1', 'u-1', CAST(:caps AS json), 'active', 1, 'u-1')"
    ), {"id": str(uuid.uuid4()), "caps": '["read_instances"]'})
    s.commit()
    from app.services.runtime.context import search_authorized
    hits = search_authorized(s, ontology_id="o-1", release_id=release_id, query="Alpha",
                             user_id="u-1")
    assert len(hits) == 1
    assert hits[0]["instance_id"] == "i-1"
    assert hits[0]["citation"]["release_id"] == release_id
    # revoke the grant -> authorized read returns empty
    s.execute(text("UPDATE ontology_data_grants SET status = 'revoked' WHERE user_id = 'u-1'"))
    s.commit()
    hits = search_authorized(s, ontology_id="o-1", release_id=release_id, query="Alpha", user_id="u-1")
    assert hits == []
    s.close()


def test_ontology_query_relations_authorized(schema):
    release_id = _seed(schema, with_release=True, with_binding=True)
    s = _session(schema)
    from app.services.publication.compiler import compile_ontology_release
    s.execute(text(
        "INSERT INTO entities (id,ontology_id,name_cn,name_en,properties,confidence,version,created_at,updated_at) "
        "VALUES ('e-2', 'o-1', '实体2', 'E2', '{}'::json, 0.9, 'v1', now(), now())"
    ))
    s.execute(text(
        "INSERT INTO entity_instances (id, entity_id, ontology_id, row_identity, row_data, created_at) "
        "VALUES ('i-1', 'e-1', 'o-1', 'a', '{\"name\":\"Alpha\"}'::json, now()),"
        "('i-2', 'e-2', 'o-1', 'b', '{\"name\":\"Beta\"}'::json, now())"
    ))
    s.execute(text(
        "INSERT INTO relations (id, ontology_id, type, source_entity, target_entity, properties, confidence, created_at) "
        "VALUES ('r-1', 'o-1', 'related', 'e-1', 'e-2', '{}'::json, 0.9, now())"
    ))
    s.commit()
    release_id = compile_ontology_release(s, ontology_id="o-1", actor_id="u-1")["release_id"]
    s.execute(text(
        "INSERT INTO entity_instance_relations (id, ontology_id, source_instance_id, target_instance_id, "
        "relation_definition_id, properties, revision, created_at, updated_at) "
        "VALUES ('ir-1', 'o-1', 'i-1', 'i-2', 'r-1', '{}'::json, 1, now(), now())"
    ))
    s.execute(text(
        "INSERT INTO ontology_data_grants (id, ontology_id, user_id, capabilities, status, revision, created_by) "
        "VALUES (:id, 'o-1', 'u-1', CAST(:caps AS json), 'active', 1, 'u-1')"
    ), {"id": str(uuid.uuid4()), "caps": '["traverse_relations"]'})
    s.commit()
    from app.services.ontology_query import query_relations
    edges = query_relations(s, ontology_id="o-1", release_id=release_id, instance_id="i-1", user_id="u-1")
    assert len(edges) == 1
    assert edges[0]["edge_id"] == "ir-1"
    assert edges[0]["relation_definition_id"] == "r-1"
    s.close()

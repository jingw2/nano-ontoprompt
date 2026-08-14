"""P5A-PREVIEW: governed instance action preview.

Descriptor + parameters -> canonical instance-only preview/hash, validated
against the pinned release and target revisions; schema-mutation parameters
are rejected; identical requests are deterministic.
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


def test_p5a_preview_red_contract():
    failures = []
    for path in ("app/services/actions/preview.py", "app/schemas/agent_actions.py"):
        p = BACKEND_DIR / path
        if not p.exists():
            failures.append(f"missing {path}")
    svc = BACKEND_DIR / "app" / "services" / "actions" / "preview.py"
    if svc.exists():
        for symbol in ("preview_action", "SCHEMA_MUTATION_REJECTED"):
            if symbol not in svc.read_text():
                failures.append(f"preview.py missing {symbol}")
    if failures:
        pytest.fail("RED_P5A_PREVIEW: " + "; ".join(failures))


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
    schema = "p5a_preview_" + uuid.uuid4().hex
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
        "VALUES ('e-1','o-1','实体','E','{}'::json,0.9,'v1',now(),now())"
    ))
    s.commit()
    release = compile_ontology_release(s, ontology_id="o-1", actor_id="u-1")
    s.execute(text(
        "INSERT INTO entity_instances (id, entity_id, ontology_id, row_identity, row_data, created_at) "
        "VALUES ('i-1', 'e-1', 'o-1', 'a', '{\"name\":\"Alpha\"}'::json, now())"
    ))
    s.commit()
    s.close()
    return release["release_id"]


def test_preview_canonical_hash_deterministic(schema):
    release_id = _seed(schema)
    s = _session(schema)
    from app.services.actions.preview import preview_action
    p1 = preview_action(s, actor_id="u-1", agent_id="a-1", ontology_id="o-1",
                        release_id=release_id, descriptor_id="action.flag",
                        parameters={"note": "review", "target": "x"},
                        target_instance_id="i-1")
    p2 = preview_action(s, actor_id="u-1", agent_id="a-1", ontology_id="o-1",
                        release_id=release_id, descriptor_id="action.flag",
                        parameters={"note": "review", "target": "x"},
                        target_instance_id="i-1")
    assert p1["hash"] == p2["hash"]  # deterministic
    assert p1["schema_version"] == "instance-action-preview-v1"
    assert p1["release_version_no"] == 1
    assert len(p1["hash"]) == 64
    assert p1["deterministic"] is True
    s.close()


def test_preview_rejects_schema_mutation(schema):
    release_id = _seed(schema)
    s = _session(schema)
    from app.services.actions.preview import preview_action, PreviewError
    with pytest.raises(PreviewError, match="SCHEMA_MUTATION_REJECTED"):
        preview_action(s, actor_id="u-1", agent_id="a-1", ontology_id="o-1",
                       release_id=release_id, descriptor_id="action.drop",
                       parameters={"drop": "entities"})
    s.close()


def test_preview_rejects_target_outside_release(schema):
    release_id = _seed(schema)
    s = _session(schema)
    from app.services.actions.preview import preview_action, PreviewError
    with pytest.raises(PreviewError, match="TARGET_INSTANCE_NOT_IN_RELEASE"):
        preview_action(s, actor_id="u-1", agent_id="a-1", ontology_id="o-1",
                       release_id=release_id, descriptor_id="action.flag",
                       parameters={}, target_instance_id="i-ghost")
    s.close()

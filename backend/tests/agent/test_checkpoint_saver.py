"""P3B-SAVER: fenced Ontexus checkpoints + pinned serializer.

Async-only LangGraph BaseCheckpointSaver surface against
agent_turn_checkpoints / agent_turn_checkpoint_writes / agent_node_executions:
aput_writes (fenced staged writes, business_committed -> writes_staged),
aput (immutable child + consume staged rows -> checkpoint_committed),
aget_tuple/alist/adelete_thread (terminal + purge marker only), and the
tagged canonical serializer (round-trip; unknown/secret/pickle fails closed).
"""
import asyncio
import datetime
import decimal
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


def test_p3b_saver_red_contract():
    failures = []
    for path in ("app/runtime/checkpoint.py", "app/runtime/serializer.py"):
        p = BACKEND_DIR / path
        if not p.exists():
            failures.append(f"missing {path}")
    saver = BACKEND_DIR / "app" / "runtime" / "checkpoint.py"
    if saver.exists():
        for symbol in ("aget_tuple", "alist", "aput", "aput_writes", "adelete_thread",
                       "get_next_version", "SYNC_CHECKPOINTER_UNSUPPORTED"):
            if symbol not in saver.read_text():
                failures.append(f"checkpoint.py missing {symbol}")
    ser = BACKEND_DIR / "app" / "runtime" / "serializer.py"
    if ser.exists():
        for symbol in ("dumps", "loads", "UNSERIALIZABLE_CHECKPOINT_VALUE"):
            if symbol not in ser.read_text():
                failures.append(f"serializer.py missing {symbol}")
    if failures:
        pytest.fail("RED_P3B_SAVER: " + "; ".join(failures))


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
    schema = "p3b_saver_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    assert _alembic(schema, "upgrade", "0006_agent_runtime").returncode == 0
    yield schema
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def _seed(session, *, turn_id="t-1", agent_id="a-1", editor_id="u-1", status="running"):
    session.execute(text(
        "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
        "VALUES (:u,'s','s@t.com','h','editor',true,:d,now(),now())"
    ), {"u": editor_id, "d": DEFAULT_DOMAIN})
    session.execute(text(
        "INSERT INTO ontology_projects (id,name,domain,version,status,created_by,created_at,updated_at,security_domain_id,working_revision) "
        "VALUES ('o-1','O','test','v1','created',:u,now(),now(),:d,1)"
    ), {"u": editor_id, "d": DEFAULT_DOMAIN})
    session.execute(text(
        "INSERT INTO agents (id,visibility,status,owner_id,created_at,updated_at) "
        "VALUES (:id,'private','active',:u,now(),now())"
    ), {"id": agent_id, "u": editor_id})
    session.execute(text(
        "INSERT INTO agent_sessions (id, agent_id, owner_user_id, status) "
        "VALUES ('s-1', :agent, :u, 'active')"
    ), {"agent": agent_id, "u": editor_id})
    session.execute(text(
        "INSERT INTO agent_turns (id, session_id, status, created_at, updated_at) "
        "VALUES (:tid, 's-1', :status, now(), now())"
    ), {"tid": turn_id, "status": status})
    session.commit()


def _saver(schema, serde=None):
    from app.runtime.checkpoint import OntexusCheckpointSaver
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
    return OntexusCheckpointSaver(
        session_factory=sessionmaker(bind=create_engine(_scoped_url(schema))),
        serde=serde or JsonPlusSerializer(),
    )


def _cfg(thread_id="t-1", checkpoint_id=None, ns="agent-v1"):
    return {"configurable": {
        "thread_id": thread_id, "checkpoint_ns": ns,
        **({"checkpoint_id": checkpoint_id} if checkpoint_id else {}),
    }}


def test_serializer_round_trips_tagged_values():
    from app.runtime.serializer import dumps, loads
    value = {
        "str": "hello", "int": 42, "float": 1.5, "bool": True, "none": None,
        "uuid": uuid.uuid4(), "decimal": decimal.Decimal("3.14"),
        "datetime": datetime.datetime.now(datetime.timezone.utc),
        "bytes": b"\x00\x01", "tuple": (1, "a"), "list": [1, 2], "dict": {"k": "v"},
    }
    restored = loads(dumps(value))
    assert restored["str"] == "hello"
    assert restored["int"] == 42
    assert restored["uuid"] == value["uuid"]
    assert restored["decimal"] == value["decimal"]
    assert restored["datetime"] == value["datetime"]
    assert restored["bytes"] == b"\x00\x01"
    assert restored["tuple"] == (1, "a")


def test_serializer_rejects_unsafe_values():
    from app.runtime.serializer import dumps, UnserializableCheckpointValue

    class Secret:
        pass

    with pytest.raises(UnserializableCheckpointValue):
        dumps({"secret": Secret()})
    with pytest.raises(UnserializableCheckpointValue):
        dumps(lambda x: x)
    with pytest.raises(UnserializableCheckpointValue):
        dumps({"naive": datetime.datetime(2026, 1, 1)})


def test_sync_methods_raise_sync_unsupported(schema):
    saver = _saver(schema)
    with pytest.raises(NotImplementedError, match="SYNC_CHECKPOINTER_UNSUPPORTED"):
        saver.get_tuple(_cfg())
    with pytest.raises(NotImplementedError, match="SYNC_CHECKPOINTER_UNSUPPORTED"):
        saver.put(_cfg(), None, None, {})  # type: ignore[arg-type]
    with pytest.raises(NotImplementedError, match="SYNC_CHECKPOINTER_UNSUPPORTED"):
        saver.put_writes(_cfg(), [], "task")
    with pytest.raises(NotImplementedError, match="SYNC_CHECKPOINTER_UNSUPPORTED"):
        saver.delete_thread("t-1")


def test_aput_writes_then_aput_call_order(schema):
    """Contract spies: staged writes commit first (business_committed ->
    writes_staged), then aput inserts the child and consumes them
    (checkpoint_committed)."""
    session = sessionmaker(bind=create_engine(_scoped_url(schema)))()
    _seed(session)
    session.close()

    from langgraph.checkpoint.base import Checkpoint, CheckpointMetadata
    saver = _saver(schema)

    async def run():
        # aput_writes for parent checkpoint "p1"
        await saver.aput_writes(_cfg(checkpoint_id="p1"), [("messages", [{"role": "user", "content": "hi"}])], task_id="task-1")
        # aput child "c1" on top of "p1"
        checkpoint = Checkpoint(
            v=2, id="c1", ts="2026-08-14T00:00:00+00:00",
            channel_values={"messages": [{"role": "user", "content": "hi"}]},
            channel_versions={}, versions_seen={}, updated_channels=[],
        )
        metadata = CheckpointMetadata(source="loop", step=1, parents={}, run_id=None,
                                      counters_since_delta_snapshot=0)
        child_cfg = await saver.aput(_cfg(checkpoint_id="p1"), checkpoint, metadata, {})
        return child_cfg

    child_cfg = asyncio.run(run())
    assert child_cfg["configurable"]["checkpoint_id"] == "c1"

    session = sessionmaker(bind=create_engine(_scoped_url(schema)))()
    # staged write consumed by c1, attempt checkpoint_committed
    consumed = session.execute(text(
        "SELECT consumed_child_checkpoint_id, attempt_state FROM agent_turn_checkpoint_writes "
        "WHERE parent_checkpoint_id = 'p1'"
    )).mappings().one()
    assert consumed["consumed_child_checkpoint_id"] == "c1"
    assert consumed["attempt_state"] == "checkpoint_committed"
    node_state = session.execute(text(
        "SELECT state FROM agent_node_executions WHERE task_id = 'task-1'"
    )).scalar_one()
    assert node_state == "checkpoint_committed"
    # immutable child exists
    assert session.execute(text(
        "SELECT count(*) FROM agent_turn_checkpoints WHERE checkpoint_id = 'c1'"
    )).scalar_one() == 1
    session.close()


def test_aget_tuple_returns_parent_with_pending_writes(schema):
    session = sessionmaker(bind=create_engine(_scoped_url(schema)))()
    _seed(session)
    session.close()
    saver = _saver(schema)

    async def run():
        await saver.aput_writes(_cfg(checkpoint_id="p1"), [("messages", "w")], task_id="task-1")
        return await saver.aget_tuple(_cfg(checkpoint_id="p1"))

    tup = asyncio.run(run())
    assert tup is not None
    assert tup.checkpoint["id"] == "p1"
    assert len(tup.pending_writes) == 1


def test_adelete_thread_requires_terminal_and_marker(schema):
    session = sessionmaker(bind=create_engine(_scoped_url(schema)))()
    _seed(session, status="running")
    session.close()
    saver = _saver(schema)

    async def run():
        with pytest.raises(Exception, match="CHECKPOINT_DELETE_FORBIDDEN"):
            await saver.adelete_thread("t-1")

    asyncio.run(run())
    # terminal + marker -> allowed
    session = sessionmaker(bind=create_engine(_scoped_url(schema)))()
    session.execute(text("UPDATE agent_turns SET status = 'succeeded' WHERE id = 't-1'"))
    session.execute(text(
        "INSERT INTO security_domains (id, key, status, created_at) "
        "VALUES (:id, 'default', 'active', now()) ON CONFLICT DO NOTHING"
    ), {"id": DEFAULT_DOMAIN})
    session.execute(text(
        "INSERT INTO agent_purge_jobs (id, security_domain_id, purge_class, cursor_time, batch_size, generation) "
        "VALUES (:id, :dom, 'turn', now(), 500, 1)"
    ), {"id": "j-1", "dom": DEFAULT_DOMAIN})
    session.execute(text(
        "INSERT INTO agent_purge_markers (id, turn_id, fixed_policy_hash, job_id, generation, created_at) "
        "VALUES (:id, 't-1', 'h', 'j-1', 1, now())"
    ), {"id": str(uuid.uuid4())})
    session.commit()
    session.close()
    saver2 = _saver(schema)

    async def run2():
        await saver2.aput_writes(_cfg(checkpoint_id="p1"), [("messages", "w")], task_id="task-1")
        await saver2.adelete_thread("t-1")

    asyncio.run(run2())
    session = sessionmaker(bind=create_engine(_scoped_url(schema)))()
    assert session.execute(text("SELECT count(*) FROM agent_turn_checkpoint_writes")).scalar_one() == 0
    session.close()


def test_repeated_aput_writes_do_not_duplicate(schema):
    session = sessionmaker(bind=create_engine(_scoped_url(schema)))()
    _seed(session)
    session.close()
    saver = _saver(schema)

    async def run():
        await saver.aput_writes(_cfg(checkpoint_id="p1"), [("messages", "w")], task_id="task-1")
        await saver.aput_writes(_cfg(checkpoint_id="p1"), [("messages", "w")], task_id="task-1")

    asyncio.run(run())
    session = sessionmaker(bind=create_engine(_scoped_url(schema)))()
    assert session.execute(text("SELECT count(*) FROM agent_turn_checkpoint_writes "
                                "WHERE parent_checkpoint_id = 'p1'")).scalar_one() == 1
    session.close()

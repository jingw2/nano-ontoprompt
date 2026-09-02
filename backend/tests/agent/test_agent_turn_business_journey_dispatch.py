"""Task 4 fix: the real browser turn-dispatch path actually routes a
business-journey Agent's turn through `LangGraphRuntime`'s dedicated
`DeepSeekVisionCaller` protocol.

Before this fix, `assemble_turn_context`'s own docstring admitted "no
production caller of this function passes [business_journey] yet" —
`app.tasks.agent_turn.agent_turn_execute` (the real dispatch entrypoint a
browser-created Turn goes through) built its context with no journey
information at all, so a real browser turn always took the generic,
provider-agnostic tool-calling path, never the journey-aware one. Every
journey-specific persisted event (`call_kind`-tagged `model_call`,
`tool_execution_id`, a `receipt_id`-bearing `final_response`) was therefore
structurally unreachable from a real turn.

The fix threads the association through the ONE place it can live without
inventing a parallel request field or browser-visible signal: the Agent's
own pinned, immutable `model_config_versions.options` JSON — tagged once,
at creation time, by `evals.business_journeys.api_client.JourneyApiClient.
create_model_config` (the only place a business-journey model
configuration is ever minted). `app.tasks.agent_turn._resolve_business_
journey` reads it straight back during dispatch.

This module proves two things a real browser turn depends on:
  1. `_resolve_business_journey` correctly parses that tag in every shape
     it can arrive in (dict on PostgreSQL, JSON string on SQLite, absent
     for an ordinary non-journey Agent) — pure unit tests.
  2. The REAL dispatch query (`_load_turn_dispatch_row`, extracted
     verbatim from `agent_turn_execute`) actually surfaces that tag for a
     genuinely seeded Agent/Version/Session/Turn row set — proving the
     JOIN, not just the parsing function in isolation.
"""
from __future__ import annotations

import json
import uuid

from sqlalchemy import text

from app.tasks.agent_turn import _load_turn_dispatch_row, _resolve_business_journey

RUN_ID = "journey-dispatch-test"
JOURNEY_ID = "supply_chain"


# ---------------------------------------------------------------------------
# 1. `_resolve_business_journey` — pure parsing, every real shape.
# ---------------------------------------------------------------------------

def test_resolves_a_real_business_journey_dict():
    options = {"temperature": 0, "seed": 0, "business_journey": {"run_id": RUN_ID, "journey_id": JOURNEY_ID}}
    assert _resolve_business_journey(options) == {"run_id": RUN_ID, "journey_id": JOURNEY_ID}


def test_resolves_a_business_journey_json_string_the_sqlite_dialect_can_return():
    options_json = json.dumps({"business_journey": {"run_id": RUN_ID, "journey_id": JOURNEY_ID}})
    assert _resolve_business_journey(options_json) == {"run_id": RUN_ID, "journey_id": JOURNEY_ID}


def test_an_ordinary_non_journey_model_config_resolves_to_none():
    """An Agent pinned to a normal, non-business-journey model configuration
    must behave exactly as it does today — no accidental opt-in."""
    assert _resolve_business_journey({"temperature": 0.7}) is None
    assert _resolve_business_journey({}) is None
    assert _resolve_business_journey(None) is None
    assert _resolve_business_journey("") is None


def test_a_partial_or_malformed_tag_resolves_to_none_not_a_crash():
    assert _resolve_business_journey({"business_journey": {"run_id": RUN_ID}}) is None  # missing journey_id
    assert _resolve_business_journey({"business_journey": "not-a-dict"}) is None
    assert _resolve_business_journey({"business_journey": {"run_id": "", "journey_id": JOURNEY_ID}}) is None
    assert _resolve_business_journey("not valid json{") is None


# ---------------------------------------------------------------------------
# 2. `_load_turn_dispatch_row` — the real dispatch JOIN, against a genuinely
#    seeded Agent/Version/Session/Turn row set (the exact query
#    `agent_turn_execute` runs, extracted so it is directly testable without
#    the full claim/runtime/finalize machinery that function also needs).
# ---------------------------------------------------------------------------

def _seed_turn(db, *, model_config_options: dict | None) -> str:
    """Seeds the minimal real row set `_load_turn_dispatch_row`'s query
    joins across — raw SQL, matching the style already used throughout
    `app.services.runtime`/`evals.business_journeys` test modules, so a
    JSON column round-trips through this SQLite harness exactly the way
    `_resolve_business_journey`'s own dialect-duality handling expects
    (a JSON-encoded string, not a live Python dict, unlike the ORM path)."""
    user_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    turn_id = str(uuid.uuid4())
    model_config_id = str(uuid.uuid4())
    model_version_id = str(uuid.uuid4())

    db.execute(text(
        "INSERT INTO users (id, username, email, password_hash, role, is_active, security_domain_id, "
        "created_at, updated_at) "
        "VALUES (:id, :u, :e, 'x', 'editor', 1, '00000000-0000-0000-0000-000000000001', "
        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ), {"id": user_id, "u": f"u-{user_id[:8]}", "e": f"{user_id[:8]}@test.invalid"})

    db.execute(text(
        "INSERT INTO model_configs (id, name, config_type, provider, models, options, created_by, "
        "created_at, updated_at) "
        "VALUES (:id, 'business-journey-supply_chain', 'llm', 'deepseek', :models, :options, :owner, "
        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ), {"id": model_config_id, "models": json.dumps(["deepseek-v4-flash-vision-exp"]),
        "options": json.dumps({}), "owner": user_id})
    db.execute(text(
        "INSERT INTO model_config_versions (id, model_config_id, version_no, provider, api_base, options, "
        "behavior_hash, model_contract, created_at) "
        "VALUES (:id, :config, 1, 'deepseek', 'https://api.deepseek.com', :options, 'hash', :contract, "
        "CURRENT_TIMESTAMP)"
    ), {"id": model_version_id, "config": model_config_id,
        "options": json.dumps(model_config_options) if model_config_options is not None else json.dumps({}),
        "contract": json.dumps([{"provider_model_revision": "deepseek-v4-flash-vision-exp"}])})

    db.execute(text(
        "INSERT INTO agents (id, visibility, status, owner_id, active_version_id, created_at, updated_at) "
        "VALUES (:id, 'private', 'active', :owner, :version, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ), {"id": agent_id, "owner": user_id, "version": version_id})
    db.execute(text(
        "INSERT INTO agent_versions (id, agent_id, version_no, name, default_model_config_version_id, "
        "default_model_name, memory_settings, application_state_schema_version_id, config_hash, created_by, "
        "created_at) "
        "VALUES (:id, :agent, 1, 'test agent', :mvid, 'business-journey-supply_chain', :mem, 'schema-dummy', "
        "'hash-dummy', :owner, CURRENT_TIMESTAMP)"
    ), {"id": version_id, "agent": agent_id, "mvid": model_version_id, "mem": json.dumps({}), "owner": user_id})

    db.execute(text(
        "INSERT INTO agent_sessions (id, agent_id, owner_user_id, status, created_at, updated_at) "
        "VALUES (:id, :agent, :owner, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ), {"id": session_id, "agent": agent_id, "owner": user_id})
    db.execute(text(
        "INSERT INTO agent_turns (id, session_id, status, dispatch_generation, created_at, updated_at) "
        "VALUES (:id, :session, 'queued', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ), {"id": turn_id, "session": session_id})
    db.commit()
    return turn_id


def test_a_real_business_journey_agents_turn_resolves_business_journey_from_the_dispatch_query(db):
    turn_id = _seed_turn(db, model_config_options={
        "temperature": 0, "seed": 0, "business_journey": {"run_id": RUN_ID, "journey_id": JOURNEY_ID},
    })

    row = _load_turn_dispatch_row(db, turn_id=turn_id)
    business_journey = _resolve_business_journey(row["model_config_options"])

    assert business_journey == {"run_id": RUN_ID, "journey_id": JOURNEY_ID}


def test_an_ordinary_agents_turn_never_resolves_a_business_journey_from_the_dispatch_query(db):
    """The exact same query/parsing path, for an Agent pinned to a normal
    (non-business-journey) model configuration — must resolve to `None` so
    an ordinary human turn is completely unaffected by this wiring."""
    turn_id = _seed_turn(db, model_config_options={"temperature": 0.7})

    row = _load_turn_dispatch_row(db, turn_id=turn_id)
    business_journey = _resolve_business_journey(row["model_config_options"])

    assert business_journey is None

"""P2B-POLICY: closed capability policy, operation map and catalog filters.

The restricted row-policy DSL accepts only the closed operator/actor-claim
vocabulary and caps node depth/value size; unknown operations, capabilities,
operators or claims fail closed.  Agent catalogs expose only redacted items
the principal's capabilities and role ceiling permit.
"""
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]


def test_p2b_policy_red_contract():
    failures = []
    policy = BACKEND_DIR / "app" / "services" / "agent" / "policy.py"
    if not policy.exists():
        failures.append("missing app/services/agent/policy.py")
    else:
        source = policy.read_text()
        for symbol in ("compile_row_policy", "evaluate_row_policy", "operation_capabilities", "ALLOWED_OPERATORS"):
            if symbol not in source:
                failures.append(f"policy.py missing {symbol}")
    catalog = BACKEND_DIR / "app" / "services" / "agent" / "catalog.py"
    if not catalog.exists():
        failures.append("missing app/services/agent/catalog.py")
    if failures:
        pytest.fail("RED_P2B_POLICY: " + "; ".join(failures))


def test_dsl_closed_vocabulary_and_compile_denies_unknown():
    from app.services.agent.policy import ALLOWED_ACTOR_CLAIMS, ALLOWED_OPERATORS, PolicyDslInvalid, compile_row_policy
    assert ALLOWED_OPERATORS == {"eq", "ne", "in", "lt", "lte", "gt", "gte", "and", "or"}
    assert ALLOWED_ACTOR_CLAIMS == {
        "actor.security_domain_id", "actor.user_id", "actor.role",
        "actor.authentication_time", "actor.token_id",
    }
    # valid policies compile
    compile_row_policy({"and": [
        {"property": "owner_id", "op": "eq", "value_from": "actor.user_id"},
        {"property": "status", "op": "ne", "value": "sealed"},
    ]})
    compile_row_policy({"property": "row_count", "op": "gte", "value": 10})
    # closed-unknown operator fails closed
    with pytest.raises(PolicyDslInvalid):
        compile_row_policy({"property": "status", "op": "like", "value": "%x%"})
    with pytest.raises(PolicyDslInvalid):
        compile_row_policy({"property": "status", "op": "eq", "value_from": "actor.region"})
    with pytest.raises(PolicyDslInvalid):
        compile_row_policy({"property": "status", "op": "eq", "value": "x" * 600})
    with pytest.raises(PolicyDslInvalid):
        compile_row_policy({"and": []})
    with pytest.raises(PolicyDslInvalid):
        compile_row_policy("SELECT * FROM users")
    with pytest.raises(PolicyDslInvalid):
        compile_row_policy({"property": "status", "op": "eq"})  # no value/value_from


def test_dsl_evaluation_matrix():
    from app.services.agent.policy import PolicyDslInvalid, PolicyEvaluationError, compile_row_policy, evaluate_row_policy
    actor = {"actor.user_id": "u-1", "actor.role": "editor", "actor.security_domain_id": "d-1"}
    row = {"owner_id": "u-1", "status": "open", "row_count": 42}
    policy = compile_row_policy({"and": [
        {"property": "owner_id", "op": "eq", "value_from": "actor.user_id"},
        {"property": "status", "op": "ne", "value": "sealed"},
    ]})
    assert evaluate_row_policy(policy, row, actor) is True
    assert evaluate_row_policy(policy, {**row, "owner_id": "u-2"}, actor) is False
    assert evaluate_row_policy(policy, {**row, "status": "sealed"}, actor) is False
    # or-branch
    or_policy = compile_row_policy({"or": [
        {"property": "row_count", "op": "gte", "value": 100},
        {"property": "status", "op": "eq", "value": "open"},
    ]})
    assert evaluate_row_policy(or_policy, row, actor) is True
    # in
    in_policy = compile_row_policy({"property": "status", "op": "in", "value": ["open", "review"]})
    assert evaluate_row_policy(in_policy, row, actor) is True
    # missing row property -> False (cannot prove the predicate)
    missing = compile_row_policy({"property": "region", "op": "eq", "value": "cn"})
    assert evaluate_row_policy(missing, row, actor) is False
    # missing referenced actor claim -> evaluation fails closed
    with pytest.raises(PolicyEvaluationError):
        evaluate_row_policy(policy, row, {})
    # None policy allows
    assert evaluate_row_policy(None, row, actor) is True


def test_role_data_capability_ceilings():
    from app.services.agent.policy import role_data_capability_ceiling
    viewer = role_data_capability_ceiling("viewer")
    assert "execute_read_logic" in viewer
    assert "preview_instance_action" not in viewer
    assert "execute_instance_action" not in viewer
    editor = role_data_capability_ceiling("editor")
    assert "execute_instance_action" in editor
    admin = role_data_capability_ceiling("admin")
    assert admin == editor
    # legacy role maps to viewer ceiling
    assert role_data_capability_ceiling("user") == viewer
    assert role_data_capability_ceiling("junk") == viewer


def test_operation_map_covers_all_registered_routes():
    from app.main import app
    from app.services.agent.policy import operation_plane
    from fastapi.routing import APIRoute

    unmapped = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in (route.methods or set()):
            if operation_plane(method, route.path) == "unknown":
                unmapped.append(f"{method} {route.path}")
    assert unmapped == [], f"POLICY_UNMAPPED_OPERATION: {unmapped}"


def test_unknown_operation_fails_closed():
    from app.services.agent.policy import operation_capabilities
    from app.services.agent.catalog import unknown_capability_fails_closed
    assert operation_capabilities("DELETE", "/api/v1/ontologies/x") == frozenset()
    assert operation_capabilities("POST", "/api/v2/ontologies/{ontology_id}/graph/cypher") == frozenset({"traverse_relations"})
    assert unknown_capability_fails_closed({"discover", "run"}) is True
    assert unknown_capability_fails_closed({"discover", "sudo"}) is False

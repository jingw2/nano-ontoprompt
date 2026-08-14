"""Agent capability policy (P2B-POLICY).

Closed vocabularies, exact role ceilings, the `OntologyOperationPolicy` code
mapping from every runtime REST operation to one or more data-capability
values, and the restricted row-policy DSL evaluator.  Unknown operations,
capabilities, operators, actor claims, or properties fail closed — never an
implicit allow.
"""
from __future__ import annotations

from typing import Any

from app.services.authorization import normalize_role

DSL_VERSION = "restricted-policy-dsl-v1"

DATA_CAPABILITIES = frozenset({
    "discover", "read_schema", "read_instances", "traverse_relations",
    "execute_read_logic", "preview_instance_action", "execute_instance_action",
})
AGENT_CAPABILITIES = frozenset({"discover", "run", "view_config", "edit", "view_audit"})
PROJECT_CAPABILITIES = frozenset({"discover", "read", "edit", "publish"})

_DATA_ORDER = (
    "discover", "read_schema", "read_instances", "traverse_relations",
    "execute_read_logic", "preview_instance_action", "execute_instance_action",
)
# viewer capped at execute_read_logic; editor/admin at execute_instance_action
_ROLE_DATA_CEILING = {"viewer": 5, "editor": 7, "admin": 7}

ALLOWED_OPERATORS = frozenset({"eq", "ne", "in", "lt", "lte", "gt", "gte", "and", "or"})
ALLOWED_ACTOR_CLAIMS = frozenset({
    "actor.security_domain_id", "actor.user_id", "actor.role",
    "actor.authentication_time", "actor.token_id",
})
_MAX_POLICY_NODES = 32
_MAX_VALUE_SIZE = 512


class PolicyDslInvalid(Exception):
    """The restricted row-policy DSL is malformed or uses a closed-unknown
    operator/claim/property; compilation fails closed."""


class PolicyEvaluationError(Exception):
    """A runtime evaluation could not be completed; the request fails closed."""


def role_data_capability_ceiling(role: str | None) -> frozenset[str]:
    rank = _ROLE_DATA_CEILING.get(normalize_role(role), _ROLE_DATA_CEILING["viewer"])
    return frozenset(_DATA_ORDER[:rank])


def ceiling_intersection(capabilities, role: str | None) -> frozenset[str]:
    return frozenset(capabilities) & role_data_capability_ceiling(role)


# OntologyOperationPolicy — the single code mapping from every runtime REST
# method to one or more data-capability vocabulary values.  Design/lifecycle
# operations map to OntologyProjectAccessGrant (the design plane) instead.
_RUNTIME_OPERATIONS: dict[tuple[str, str], frozenset[str]] = {
    ("GET", "/api/v1/ontologies/{ontology_id}"): frozenset({"discover", "read_schema"}),
    ("GET", "/api/v1/ontologies/{ontology_id}/entities"): frozenset({"read_instances"}),
    ("GET", "/api/v1/ontologies/{ontology_id}/entities/{entity_id}/instances"): frozenset({"read_instances"}),
    ("GET", "/api/v1/ontologies/{ontology_id}/graph"): frozenset({"traverse_relations"}),
    ("GET", "/api/v1/ontologies/{ontology_id}/logic"): frozenset({"execute_read_logic"}),
    ("POST", "/api/v1/ontologies/{ontology_id}/logic/{rule_id}/test"): frozenset({"execute_read_logic"}),
    ("POST", "/api/v2/ontologies/{ontology_id}/actions/{action_id}/run"): frozenset({"execute_instance_action"}),
    ("POST", "/api/v2/ontologies/{ontology_id}/graph/cypher"): frozenset({"traverse_relations"}),
    ("POST", "/api/v2/ontologies/{ontology_id}/graph/ask"): frozenset({"traverse_relations"}),
    ("POST", "/api/v2/ontologies/{ontology_id}/search"): frozenset({"discover", "read_instances"}),
    ("GET", "/api/v2/ontologies/{ontology_id}/graph"): frozenset({"traverse_relations"}),
    ("GET", "/api/v2/ontologies/{ontology_id}/graph/quality"): frozenset({"read_schema"}),
    ("GET", "/api/v2/ontologies/{ontology_id}/integrations/status"): frozenset({"read_schema"}),
    ("GET", "/api/v2/ontologies/{ontology_id}/graph/neighbors/{node_id}"): frozenset({"traverse_relations"}),
    ("GET", "/api/v2/ontologies/{ontology_id}/graph/path"): frozenset({"traverse_relations"}),
    ("GET", "/api/v2/ontologies/{ontology_id}/graph/degree/{node_id}"): frozenset({"traverse_relations"}),
    ("GET", "/api/v2/ontologies/{ontology_id}/graph/top-nodes"): frozenset({"traverse_relations"}),
    ("GET", "/api/v2/ontologies/{ontology_id}/search/keyword"): frozenset({"discover", "read_instances"}),
    ("GET", "/api/v2/ontologies/{ontology_id}/search/semantic"): frozenset({"discover", "read_instances"}),
    ("GET", "/api/v2/ontologies/{ontology_id}/state-machines"): frozenset({"read_schema"}),
    ("GET", "/api/v2/ontologies/{ontology_id}/action-runs"): frozenset({"execute_read_logic"}),
    # I-BACKEND registration: the Agent application/runtime plane.  Session
    # owner + run grant routes classify as runtime so no registered core
    # method is "unknown"; the tool gateway only queries ontology keys above.
    ("GET", "/api/v1/agents/{agent_id}/sessions"): frozenset({"run"}),
    ("POST", "/api/v1/agents/{agent_id}/sessions"): frozenset({"run"}),
    ("GET", "/api/v1/agent-sessions/{session_id}"): frozenset({"run"}),
    ("DELETE", "/api/v1/agent-sessions/{session_id}"): frozenset({"run"}),
    ("GET", "/api/v1/agent-sessions/{session_id}/messages"): frozenset({"run"}),
    ("POST", "/api/v1/agent-sessions/{session_id}/turns"): frozenset({"run"}),
    ("GET", "/api/v1/agent-turns/{turn_id}"): frozenset({"run"}),
    ("POST", "/api/v1/agent-turns/{turn_id}/cancel"): frozenset({"run"}),
    ("POST", "/api/v1/agent-turns/{turn_id}/stream-ticket"): frozenset({"run"}),
    ("GET", "/api/v1/agent-turns/{turn_id}/events"): frozenset({"run"}),
    ("GET", "/api/v1/agent-turns/{turn_id}/stream"): frozenset({"run"}),
    ("GET", "/api/v1/agent-approvals/{approval_id}"): frozenset({"run"}),
    ("POST", "/api/v1/agent-approvals/{approval_id}/approve"): frozenset({"run"}),
    ("POST", "/api/v1/agent-approvals/{approval_id}/reject"): frozenset({"run"}),
    ("GET", "/api/v1/agent-clarifications/{clarification_id}"): frozenset({"run"}),
    ("POST", "/api/v1/agent-clarifications/{clarification_id}/answer"): frozenset({"run"}),
    ("GET", "/api/v1/agent-sessions/{session_id}/application-state"): frozenset({"run"}),
    ("POST", "/api/v1/agent-sessions/{session_id}/application-state"): frozenset({"run"}),
    ("GET", "/api/v1/agent-turns/{turn_id}/audit"): frozenset({"run"}),
}

# Endpoints that operate on the design/lifecycle plane (OntologyProjectAccessGrant).
_DESIGN_PLANE_PREFIXES = (
    "/api/v1/ontologies",
    "/api/v1/prompts",
    "/api/v1/models",
    "/api/v2/ontologies/{ontology_id}/mappings",
    "/api/v2/ontologies/{ontology_id}/link-mappings",
    "/api/v2/ontologies/{ontology_id}/graph/sync",
    "/api/v2/ontologies/{ontology_id}/logic",
    "/api/v2/ontologies/{ontology_id}/actions",
    "/api/v2/ontologies/{ontology_id}/graph/relations",
    "/api/v2/pipelines",
    "/api/v2/connections",
    "/api/v2/curated",
    "/api/v2/datasets",
)

# Platform plane: identity/administration/worker surfaces, role-based.
_PLATFORM_PREFIXES = (
    "/api/v1/auth",
    "/api/v1/users",
    "/api/v1/overview",
    "/api/v1/settings",
    "/api/v1/admin",
    "/api/v1/ontology-data-grants",
    "/api/v1/agents",
    "/api/v1/security-domains",
    "/api/v2/application-state-schemas",
    "/api/v2/incremental",
    "/health",
)


def operation_capabilities(method: str, path: str) -> frozenset[str]:
    """Data-plane vocabulary for a runtime operation; empty set (fail closed)
    when the operation is unknown."""
    return _RUNTIME_OPERATIONS.get((method.upper(), path), frozenset())


def is_design_plane(method: str, path: str) -> bool:
    return any(path.startswith(prefix) for prefix in _DESIGN_PLANE_PREFIXES)


def is_platform_plane(method: str, path: str) -> bool:
    return any(path.startswith(prefix) for prefix in _PLATFORM_PREFIXES)


def operation_plane(method: str, path: str) -> str:
    """Classify a registered operation: 'runtime' (data vocabulary), 'design'
    (project grants), 'platform' (role/identity), or 'unknown' (fail closed)."""
    if operation_capabilities(method, path):
        return "runtime"
    if is_design_plane(method, path):
        return "design"
    if is_platform_plane(method, path):
        return "platform"
    return "unknown"


def _canonicalize(value: Any) -> str:
    import json
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _coerce(value: Any, compare: Any):
    if isinstance(compare, (int, float)) and not isinstance(value, bool):
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    return value


def _resolve_operand(node: dict, row: dict, actor: dict) -> Any:
    if "value_from" in node:
        claim = node["value_from"]
        if claim not in ALLOWED_ACTOR_CLAIMS:
            raise PolicyDslInvalid(f"POLICY_DSL_INVALID actor claim {claim!r} is closed")
        if claim not in actor:
            raise PolicyEvaluationError(f"POLICY_EVAL_MISSING_CLAIM {claim}")
        return actor[claim]
    return node.get("value")


def _apply_operator(op: str, value: Any, operand: Any) -> bool:
    if op == "eq":
        return _coerce(value, operand) == _coerce(operand, value)
    if op == "ne":
        return _coerce(value, operand) != _coerce(operand, value)
    if op == "in":
        return _coerce(value, operand) in list(operand) if isinstance(operand, list) else False
    if op == "lt":
        return _coerce(value, operand) < _coerce(operand, value)
    if op == "lte":
        return _coerce(value, operand) <= _coerce(operand, value)
    if op == "gt":
        return _coerce(value, operand) > _coerce(operand, value)
    if op == "gte":
        return _coerce(value, operand) >= _coerce(operand, value)
    raise PolicyDslInvalid(f"POLICY_DSL_INVALID operator {op!r} is closed")


def _validate_dsl(node: Any, depth: int) -> None:
    if depth > _MAX_POLICY_NODES:
        raise PolicyDslInvalid("POLICY_DSL_INVALID depth exceeded")
    if not isinstance(node, dict):
        raise PolicyDslInvalid("POLICY_DSL_INVALID node must be an object")
    if "and" in node or "or" in node:
        op = "and" if "and" in node else "or"
        children = node[op]
        if not isinstance(children, list) or not children:
            raise PolicyDslInvalid(f"POLICY_DSL_INVALID {op} needs a non-empty list")
        for child in children:
            _validate_dsl(child, depth + 1)
        return
    if "property" not in node or "op" not in node:
        raise PolicyDslInvalid("POLICY_DSL_INVALID node needs property/op")
    operator = node["op"]
    if operator not in ALLOWED_OPERATORS:
        raise PolicyDslInvalid(f"POLICY_DSL_INVALID operator {operator!r} is closed")
    property_name = node["property"]
    if not isinstance(property_name, str) or not property_name or len(property_name) > 128:
        raise PolicyDslInvalid("POLICY_DSL_INVALID property is closed")
    if "value" not in node and "value_from" not in node:
        raise PolicyDslInvalid("POLICY_DSL_INVALID node needs a value or value_from")
    if "value_from" in node:
        if node["value_from"] not in ALLOWED_ACTOR_CLAIMS:
            raise PolicyDslInvalid(f"POLICY_DSL_INVALID actor claim {node['value_from']!r} is closed")
        return
    operand = node.get("value")
    if len(_canonicalize(operand)) > _MAX_VALUE_SIZE:
        raise PolicyDslInvalid("POLICY_DSL_INVALID value size exceeded")


def compile_row_policy(policy: Any) -> Any:
    """Validate the restricted row-policy DSL; raises PolicyDslInvalid on any
    closed-unknown operator/claim/property.  SQL/Cypher/code strings are
    rejected by construction (only the closed DSL shape is accepted)."""
    if policy is None:
        return None
    _validate_dsl(policy, 0)
    return policy


def evaluate_row_policy(compiled: Any, row: dict, actor: dict) -> bool:
    """Evaluate a compiled policy against one row and the immutable actor
    context.  A missing row property evaluates the comparison as False (the
    grant cannot prove the predicate); evaluation errors fail closed."""
    if compiled is None:
        return True
    if "and" in compiled:
        return all(evaluate_row_policy(child, row, actor) for child in compiled["and"])
    if "or" in compiled:
        return any(evaluate_row_policy(child, row, actor) for child in compiled["or"])
    property_name = compiled["property"]
    if property_name not in row:
        return False
    value = row[property_name]
    operand = _resolve_operand(compiled, row, actor)
    return _apply_operator(compiled["op"], value, operand)

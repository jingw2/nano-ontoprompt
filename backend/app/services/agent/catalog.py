"""Agent catalog filters (P2B-POLICY).

Agent catalogs return only what the requesting user's capabilities intersect
with the exact vocabulary and role ceilings.  Blocked/archived model
identities and ontology project grants without `discover|read` are excluded;
the response is always redacted.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.agent.policy import (
    AGENT_CAPABILITIES,
    DATA_CAPABILITIES,
    ceiling_intersection,
)
from app.services.agent.tool_categories import enrich_tool_descriptors


class CatalogEmpty(Exception):
    """No catalog items are visible to the requesting principal."""


def agent_catalog_ontologies(session: Session, user_id: str, agent_capabilities: frozenset[str]) -> list[dict]:
    """Ontologies the user can discover/read through their project grants,
    intersected with the Agent `discover` capability (design plane)."""
    if "discover" not in ceiling_intersection(agent_capabilities, None) and "discover" not in agent_capabilities:
        return []
    rows = session.execute(text(
        "SELECT o.id, o.name, o.status "
        "FROM ontology_projects o "
        "JOIN ontology_project_access_grants g ON g.ontology_id = o.id "
        "WHERE g.user_id = :uid AND g.status = 'active' "
        "AND (g.capabilities::text LIKE '%\"discover\"%' AND g.capabilities::text LIKE '%\"read\"%') "
        "ORDER BY o.name"
    ), {"uid": user_id}).mappings().all()
    return [dict(row) for row in rows]


def agent_catalog_models(session: Session, agent_capabilities: frozenset[str]) -> list[dict]:
    """Redacted active LLM identities with an immutable active version;
    blocked/archived/unversioned identities are excluded.  Never returns
    secrets or credentials."""
    if not ceiling_intersection(agent_capabilities, None) and "discover" not in agent_capabilities:
        return []
    rows = session.execute(text(
        # `id` is the ACTIVE model_config_versions.id — the exact value the Agent
        # configuration API pins as default_model_config_version_id.  Returning the
        # identity (model_configs.id) here made create/save fail with
        # MODEL_VERSION_UNAVAILABLE and would store a non-version id in the
        # immutable version row.
        "SELECT v.id AS id, mc.name, mc.provider, v.version_no, v.behavior_hash, v.conservative_input_limit "
        "FROM model_configs mc "
        "JOIN model_config_versions v ON v.id = mc.active_version_id "
        "WHERE mc.config_type = 'llm' AND mc.status = 'active' AND mc.active_version_id IS NOT NULL "
        "ORDER BY mc.name"
    )).mappings().all()
    return [dict(row) for row in rows]


def validate_agent_tools(capabilities: frozenset[str], operation_capabilities: frozenset[str]) -> bool:
    """Tool configuration is valid only when the requested operation's data
    vocabulary is fully within the principal's effective capabilities (the
    caller passes the principal's own granted capabilities, never a union of
    unrelated vocabularies)."""
    if not operation_capabilities:
        return False
    return operation_capabilities <= frozenset(capabilities)


def unknown_capability_fails_closed(capabilities) -> bool:
    """Any capability outside the closed vocabularies is rejected outright."""
    known = AGENT_CAPABILITIES | DATA_CAPABILITIES
    return not (set(capabilities) - known)


def ontology_tool_catalog(db: Session, ontology_id: str) -> dict:
    """A published Ontology's exposed tools (P2B-TOOLS exposure).

    Reads the immutable latest release manifest's `tool_descriptors` when the
    Ontology is published; otherwise derives the same descriptor set from the
    working copy (enabled Logic rules + Actions + built-in query) so tool
    selection works before the first publish.  Returns a flat descriptor list
    with stable `descriptor_id` values (`query:<id>`, `logic:<id>`,
    `action:<id>`) plus a `published` flag and `release_id` when available.
    """
    release = db.execute(text(
        "SELECT r.id AS release_id, r.manifest_projection FROM ontology_projects p "
        "JOIN ontology_releases r ON r.id = p.latest_published_release_id "
        "WHERE p.id = :o"
    ), {"o": ontology_id}).mappings().one_or_none()
    if release is not None:
        projection = release["manifest_projection"]
        if isinstance(projection, (str, bytes, bytearray)):
            import json
            projection = json.loads(projection)
        return {
            "ontology_id": ontology_id,
            "published": True,
            "release_id": release["release_id"],
            "tools": enrich_tool_descriptors(list(projection.get("tool_descriptors", []))),
        }
    return {
        "ontology_id": ontology_id,
        "published": False,
        "release_id": None,
        "tools": _working_copy_tool_descriptors(db, ontology_id),
    }


def _working_copy_tool_descriptors(db: Session, ontology_id: str) -> list[dict]:
    """Mirror of the compiler's `_tool_descriptors` for unpublished Ontologies
    (deterministic ids, same shape as the release manifest descriptors)."""
    import hashlib
    descriptors: list[dict] = [{
        "descriptor_id": f"query:{ontology_id}",
        "version": 1,
        "source_kind": "builtin",
        "source_id": "query",
        "input_schema": {"query": {"type": "string"}},
        "output_schema": {"results": {"type": "array"}},
        "capability": "read_instances",
        "timeout_ms": 10_000,
        "result_limit": 10,
        "descriptor_hash": hashlib.sha256(f"query:{ontology_id}".encode()).hexdigest(),
    }]
    logic = db.execute(text(
        "SELECT id, name, version FROM v2_ontology_logic_rules "
        "WHERE ontology_id = :o AND enabled = true ORDER BY id"
    ), {"o": ontology_id}).mappings().all()
    for rule in logic:
        descriptors.append({
            "descriptor_id": f"logic:{rule['id']}", "version": rule["version"],
            "source_kind": "logic", "source_id": rule["id"],
            "input_schema": {"entity_type": {"type": "string"}, "parameters": {"type": "object"}},
            "output_schema": {"result": {"type": "object"}},
            "capability": "execute_read_logic", "timeout_ms": 10_000, "result_limit": 1,
            "descriptor_hash": hashlib.sha256(f"logic:{rule['id']}".encode()).hexdigest(),
        })
    actions = db.execute(text(
        "SELECT id, name, version FROM v2_ontology_action_types "
        "WHERE ontology_id = :o AND enabled = true ORDER BY id"
    ), {"o": ontology_id}).mappings().all()
    for action in actions:
        descriptors.append({
            "descriptor_id": f"action:{action['id']}", "version": action["version"],
            "source_kind": "action", "source_id": action["id"],
            "input_schema": {"parameters": {"type": "object"}},
            "output_schema": {"result": {"type": "object"}},
            "capability": "execute_instance_action", "timeout_ms": 30_000, "result_limit": 1,
            "descriptor_hash": hashlib.sha256(f"action:{action['id']}".encode()).hexdigest(),
        })
    return enrich_tool_descriptors(descriptors)


def validate_binding_tools(db: Session, ontology_id: str, selected_tools: list[str]) -> bool:
    """Every selected tool descriptor must be exposed by the Ontology (fail
    closed on unknown ids)."""
    if not selected_tools:
        return True
    available = {t["descriptor_id"] for t in ontology_tool_catalog(db, ontology_id)["tools"]}
    return set(selected_tools) <= available

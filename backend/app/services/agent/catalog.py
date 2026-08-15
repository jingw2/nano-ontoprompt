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
    vocabulary is fully within the principal's capabilities and role ceiling."""
    if not operation_capabilities:
        return False
    return operation_capabilities <= ceiling_intersection(capabilities, None) or operation_capabilities <= capabilities


def unknown_capability_fails_closed(capabilities) -> bool:
    """Any capability outside the closed vocabularies is rejected outright."""
    known = AGENT_CAPABILITIES | DATA_CAPABILITIES
    return not (set(capabilities) - known)

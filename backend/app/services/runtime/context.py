"""Pinned Agent context assembler (P4A-CONTEXT).

Resolves the latest-once pinned tuple for a Turn — release, model, retrieval
sources and tool bindings — from authoritative rows, with deterministic
budgets, SQL refetch and citation-bearing authorized reads.  The current Turn
keeps the release pinned at creation; the next Turn refreshes to the latest
published release.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

DEFAULT_MESSAGE_BUDGET = 12
DEFAULT_CONTEXT_BUDGET = 24_000  # approximate input token budget


class ContextError(Exception):
    """Context assembly failed (fail closed)."""


@dataclass(frozen=True)
class PinnedContext:
    turn_id: str
    agent_id: str
    release_id: str | None
    release_version_no: int | None
    release_schema_hash: str | None
    model_config_version_id: str
    model_name: str
    ontology_ids: tuple[str, ...] = ()
    retrieval_sources: tuple[dict, ...] = ()
    tool_bindings: tuple[dict, ...] = ()
    skill_bindings: tuple[dict, ...] = ()
    ontology_tool_selection: tuple[dict, ...] = ()  # per-binding enabled tools
    budgets: dict[str, int] = field(default_factory=lambda: {
        "messages": DEFAULT_MESSAGE_BUDGET, "context": DEFAULT_CONTEXT_BUDGET,
        "summary": 1200, "recall": 800,
    })
    citations: tuple[dict, ...] = ()


def _release_for_ontology(db: Session, ontology_id: str) -> dict | None:
    return db.execute(text(
        "SELECT r.id, r.version_no, r.schema_hash, r.manifest_projection "
        "FROM ontology_projects p "
        "JOIN ontology_releases r ON r.id = p.latest_published_release_id "
        "WHERE p.id = :o"
    ), {"o": ontology_id}).mappings().one_or_none()


def resolve_pinned_context(db: Session, *, turn_id: str, session_id: str) -> PinnedContext:
    """Latest-once resolution: read the agent's active version + ontology
    bindings and the latest published release; the caller pins these for the
    Turn's duration."""
    base = db.execute(text(
        "SELECT t.id AS turn_id, s.agent_id, v.id AS version_id, "
        "v.default_model_config_version_id, v.default_model_name "
        "FROM agent_turns t "
        "JOIN agent_sessions s ON s.id = t.session_id "
        "JOIN agents a ON a.id = s.agent_id "
        "JOIN agent_versions v ON v.id = a.active_version_id "
        "WHERE t.id = :tid AND s.id = :sid"
    ), {"tid": turn_id, "sid": session_id}).mappings().one_or_none()
    if base is None:
        raise ContextError("TURN_NOT_FOUND")

    bindings = db.execute(text(
        "SELECT ontology_id, capabilities, allowlists, selected_tools, enabled_categories "
        "FROM agent_ontology_bindings "
        "WHERE agent_version_id = :vid ORDER BY ontology_id"
    ), {"vid": base["version_id"]}).mappings().all()
    ontology_ids = tuple(b["ontology_id"] for b in bindings)
    tool_selection = []
    for b in bindings:
        entry = {
            "ontology_id": b["ontology_id"],
            "capabilities": list(b["capabilities"] or []),
            "selected_tools": list(b["selected_tools"] or []),
        }
        # category-mode filtering: present only when the binding stores it
        # (None keeps the legacy selected_tools-only filter)
        if b["enabled_categories"] is not None:
            entry["enabled_categories"] = list(b["enabled_categories"])
        tool_selection.append(entry)
    tool_selection = tuple(tool_selection)

    release = None
    if ontology_ids:
        # a Turn is single-agent/single-ontology in core MVP; use the first binding
        release = _release_for_ontology(db, ontology_ids[0])

    retrieval = db.execute(text(
        "SELECT source_id, revision, kind, config_hash, applicability_hash "
        "FROM agent_retrieval_sources WHERE agent_version_id = :vid ORDER BY source_id"
    ), {"vid": base["version_id"]}).mappings().all()
    tools = db.execute(text(
        "SELECT tool_connection_version_id, alias FROM agent_external_tool_bindings "
        "WHERE agent_version_id = :vid ORDER BY alias"
    ), {"vid": base["version_id"]}).mappings().all()
    skills = db.execute(text(
        "SELECT skill_version_id, alias FROM agent_skill_bindings "
        "WHERE agent_version_id = :vid ORDER BY alias"
    ), {"vid": base["version_id"]}).mappings().all()

    citations: list[dict] = []
    if release is not None:
        projection = release["manifest_projection"]
        if isinstance(projection, (str, bytes, bytearray)):
            projection = json.loads(projection)
        entity_count = len(projection.get("entities", []))
        relation_count = len(projection.get("relations", []))
        citations.append({
            "type": "release", "release_id": release["id"],
            "version_no": release["version_no"], "entities": entity_count,
            "relations": relation_count,
        })

    return PinnedContext(
        turn_id=turn_id, agent_id=base["agent_id"],
        release_id=release["id"] if release else None,
        release_version_no=release["version_no"] if release else None,
        release_schema_hash=release["schema_hash"].hex() if release and release["schema_hash"] else None,
        model_config_version_id=base["default_model_config_version_id"],
        model_name=base["default_model_name"],
        ontology_ids=ontology_ids,
        retrieval_sources=tuple(dict(r) for r in retrieval),
        tool_bindings=tuple(dict(t) for t in tools),
        skill_bindings=tuple(dict(s) for s in skills),
        ontology_tool_selection=tool_selection,
        citations=tuple(citations),
    )


def search_authorized(
    db: Session, *, ontology_id: str, release_id: str, query: str, user_id: str, limit: int = 10,
) -> list[dict]:
    """SQL-refetched, release-filtered, post-authorized instance search with
    citations.  The caller must hold read_instances on the ontology data grant;
    every hit is re-fetched by id/revision and checked against the release."""
    grant = db.execute(text(
        "SELECT 1 FROM ontology_data_grants WHERE ontology_id = :o AND user_id = :u "
        "AND status = 'active' AND capabilities::text LIKE '%\"read_instances\"%' LIMIT 1"
    ), {"o": ontology_id, "u": user_id}).scalar_one_or_none()
    if not grant:
        return []
    hits = db.execute(text(
        "SELECT ei.id AS instance_id, ei.entity_id, ei.revision, ei.row_data "
        "FROM entity_instances ei "
        "WHERE ei.ontology_id = :o AND ei.deleted_at IS NULL AND ei.row_data::text ILIKE :q "
        "AND EXISTS (SELECT 1 FROM ontology_releases rel "
        "WHERE rel.id = :rid AND rel.ontology_id = ei.ontology_id "
        "AND rel.manifest_projection @> jsonb_build_object('entities', "
        "jsonb_build_array(jsonb_build_object('id', ei.entity_id)))) "
        "ORDER BY ei.updated_at DESC LIMIT :lim"
    ), {"o": ontology_id, "rid": release_id, "q": f"%{query}%", "lim": limit}).mappings().all()
    results = []
    for hit in hits:
        fresh = db.execute(text(
            "SELECT id, revision FROM entity_instances WHERE id = :id AND deleted_at IS NULL "
            "AND revision = :rev"
        ), {"id": hit["instance_id"], "rev": hit["revision"]}).mappings().one_or_none()
        if not fresh:
            continue
        results.append({
            "instance_id": hit["instance_id"], "entity_id": hit["entity_id"],
            "revision": hit["revision"], "row_data": hit["row_data"],
            "citation": {"type": "instance", "instance_id": hit["instance_id"],
                         "entity_id": hit["entity_id"], "release_id": release_id},
        })
    return results


def message_budget(context: PinnedContext) -> int:
    return context.budgets.get("messages", DEFAULT_MESSAGE_BUDGET)

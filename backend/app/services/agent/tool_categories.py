"""Ontology tool category derivation (P2B-TOOLS categories).

An Agent's ontology binding enables whole tool CATEGORIES per bound Ontology
(MCP / query / write / logic / action), defaulting to ALL.  Each exposed tool
descriptor is mapped onto exactly one category from stable surface signals
(`source_kind` + `descriptor_id` prefix) so the exposure API, the configuration
UI and the runtime all agree without changing the immutable release manifest.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

# canonical category order (UI toggle order and the runtime vocabulary)
TOOL_CATEGORIES = ("mcp", "query", "write", "logic", "action")


class ToolCategoryError(Exception):
    """A category name is outside the closed vocabulary."""


def tool_category(descriptor: dict) -> str:
    """Stable category for one tool descriptor.

    * query  = built-in ontology query descriptors (`query:<ontology_id>`)
    * logic  = executable v2 Logic rules (`logic:<rule_id>`)
    * action = instance Action preview/execute (`action:<action_id>`); a
      descriptor that explicitly marks write capability maps to `write`
    * mcp    = external MCP descriptors when present (source_kind `mcp`)
    """
    kind = descriptor.get("source_kind")
    descriptor_id = descriptor.get("descriptor_id") or ""
    if kind == "mcp" or descriptor_id.startswith("mcp:"):
        return "mcp"
    if kind == "builtin" or descriptor_id.startswith("query:"):
        return "query"
    if kind == "logic" or descriptor_id.startswith("logic:"):
        return "logic"
    if kind == "action" or descriptor_id.startswith("action:"):
        # write-capable actions (explicit write marker) -> write; the default
        # instance Action preview/execute stays in the action category
        if descriptor.get("mode") == "write" or descriptor.get("write") is True:
            return "write"
        return "action"
    # unknown shapes default to the action bucket (fail-open display only; the
    # runtime still requires an explicit binding category)
    return "action"


def _live_names(db: Session, tools: list[dict]) -> dict[str, str]:
    """Current Logic-rule/Action names keyed by source_id.

    A published release's `tool_descriptors` are frozen at publish time — a
    release compiled before `name` existed on that collection (or before a
    rule/action was renamed since) has a stale or absent name baked into its
    immutable manifest bytes. Looking the live name up by `source_id` at
    serve time (never by rewriting the manifest) means the Agent
    tool-binding UI always shows the ontology's current business-rule/action
    name, including for releases published before this existed."""
    logic_ids = [d["source_id"] for d in tools if d.get("source_kind") == "logic" and d.get("source_id")]
    action_ids = [d["source_id"] for d in tools if d.get("source_kind") == "action" and d.get("source_id")]
    names: dict[str, str] = {}
    if logic_ids:
        rows = db.execute(text(
            "SELECT id, name FROM v2_ontology_logic_rules WHERE id IN :ids"
        ).bindparams(bindparam("ids", expanding=True)), {"ids": logic_ids}).all()
        names.update({row[0]: row[1] for row in rows})
    if action_ids:
        rows = db.execute(text(
            "SELECT id, name FROM v2_ontology_action_types WHERE id IN :ids"
        ).bindparams(bindparam("ids", expanding=True)), {"ids": action_ids}).all()
        names.update({row[0]: row[1] for row in rows})
    return names


def enrich_tool_descriptors(tools: list[dict], db: Session | None = None) -> list[dict]:
    """Return descriptors with the derived `category` field and, when `db` is
    given, the Logic rule/Action's CURRENT name (overriding whatever name is
    baked into the frozen manifest, if any — see `_live_names`). The release
    manifest bytes are never touched — both fields are response-only.
    `db=None` keeps category-only enrichment for callers that don't have a
    session handy (there are none in this codebase today, but the parameter
    is optional rather than widening every caller's signature for a lookup
    they may not need)."""
    live_names = _live_names(db, tools) if db is not None else {}
    enriched = []
    for d in tools:
        entry = {**d, "category": tool_category(d)}
        live_name = live_names.get(d.get("source_id"))
        if live_name is not None:
            entry["name"] = live_name
        elif not entry.get("name") and d.get("source_kind") in ("logic", "action") and d.get("source_id"):
            # the rule/action row is gone (deleted since this release was
            # published) and the frozen manifest predates `name` — fall back
            # to a readable id-based label instead of leaving it unset
            entry["name"] = f"{d['source_kind']}:{str(d['source_id'])[:8]}"
        enriched.append(entry)
    return enriched


def validate_categories(categories) -> bool:
    """Closed-vocabulary check: every category must be one of the canonical
    tool categories (fail closed on unknown names)."""
    if categories is None:
        return True
    if not isinstance(categories, list):
        return False
    return all(c in TOOL_CATEGORIES for c in categories)


def tool_enabled(binding: dict, descriptor: dict) -> bool:
    """Runtime enablement of one descriptor under an ontology binding.

    Category mode (`enabled_categories` present, possibly empty) filters by
    the descriptor's derived category; legacy bindings (no
    `enabled_categories`) keep the exact `selected_tools` filter.  The
    immutable version tree decides which mode a Turn uses."""
    categories = binding.get("enabled_categories")
    if categories is not None:
        return tool_category(descriptor) in categories
    return (descriptor.get("descriptor_id") or "") in (binding.get("selected_tools") or [])


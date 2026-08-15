"""Ontology tool category derivation (P2B-TOOLS categories).

An Agent's ontology binding enables whole tool CATEGORIES per bound Ontology
(MCP / query / write / logic / action), defaulting to ALL.  Each exposed tool
descriptor is mapped onto exactly one category from stable surface signals
(`source_kind` + `descriptor_id` prefix) so the exposure API, the configuration
UI and the runtime all agree without changing the immutable release manifest.
"""
from __future__ import annotations

from typing import Any

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


def enrich_tool_descriptors(tools: list[dict]) -> list[dict]:
    """Return descriptors with the derived `category` field.  The release
    manifest bytes are never touched — the category is response-only."""
    return [{**d, "category": tool_category(d)} for d in tools]


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


"""Mirror legacy Logic/Action rows into their v2 counterparts.

Two parallel storage paths exist for Logic rules and Actions:

* The legacy `logic_rules`/`actions` tables (`app.models.logic.LogicRule`,
  `app.models.action.Action`), written by `app/routers/logic.py` and
  `app/routers/actions.py` (the Ontology detail page's Logic/Actions tabs)
  and by the `simple_llm` build mode's LLM extraction task
  (`app/tasks/extraction.py`).
* The v2 `v2_ontology_logic_rules`/`v2_ontology_action_types` tables
  (`app.models.v2.logic.OntologyLogicRule`, `app.models.v2.action.OntologyActionType`),
  written by the `pipeline_mapping` build mode's mapping service
  (`app/services/v2/mapping/mapping_service.py`).

Publication (`app/services/publication/compiler.py`) and the Agent tool
catalog (`app/services/agent/catalog.py`) read ONLY the v2 tables — that
split was never reconciled after migration 0042 introduced the v2 schema,
so a `simple_llm` ontology's Logic rules/Actions (always legacy-table only)
never appeared as Agent tool descriptors and never appeared in a published
manifest's `logic_rules`/`actions` sections, regardless of how many rules
the Ontology's own Logic/Actions tabs showed.

These functions keep a v2 mirror row in sync with the legacy CRUD that
already exists (`app/routers/logic.py`/`app/routers/actions.py`), without
changing the legacy tables' shape or the UI that reads/writes them. A fresh
mirror shares its id with the legacy row it mirrors, which makes it trivial
to find on the next sync/delete for that row's whole lifecycle. But a
`pipeline_mapping`-mode row may ALREADY have a mirror under a DIFFERENT id
(the mapping service's own dual-write, correlated only by matching
ontology_id+name) — a lookup that only tries the legacy row's id would miss
that and spawn a duplicate the first time someone edits the row through the
legacy Logic/Actions tab. Every lookup here therefore falls back to an
ontology_id+name match before deciding no mirror exists.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.action import Action
from app.models.logic import LogicRule
from app.models.v2.action import OntologyActionType
from app.models.v2.logic import OntologyLogicRule

# the six-type v2 Logic vocabulary vs. the LLM extraction task's own four
# `function_type` values (app/tasks/extraction.py: derived_property,
# aggregation, complex_edit, external_query) — neither the DB nor the manifest
# schema enforces `logic_type` against this list (it rides into the manifest
# as a free-text `effect_classification`), so an unmapped legacy value is
# passed through as-is rather than coerced; this default only covers a
# legacy row with no `function_type` at all.
_DEFAULT_LOGIC_TYPE = "validation"
_DEFAULT_ACTION_CATEGORY = "custom"


def _synced_status(current: str, enabled: bool, fallback: str) -> str:
    """Enabled/disabled status transition only — never touches a `published`
    status (that lifecycle is owned by the v2 publish/mapping flow, not this
    mirror), matching the one status rule the toggle endpoints already had
    before this module existed."""
    if current is None:
        return fallback
    if not enabled:
        return "disabled" if current != "published" else current
    return "draft" if current == "disabled" else current


def _find_logic_mirror(db: Session, ontology_id: str, rule_id: str, name: str) -> OntologyLogicRule | None:
    mirror = db.get(OntologyLogicRule, rule_id)
    if mirror is not None:
        return mirror
    return db.execute(select(OntologyLogicRule).where(
        OntologyLogicRule.ontology_id == ontology_id, OntologyLogicRule.name == name,
    )).scalars().first()


def sync_logic_rule_to_v2(db: Session, ontology_id: str, rule: LogicRule) -> None:
    """Upsert `rule`'s v2 mirror row after a legacy create/update/toggle.
    Never overwrites a `published` mirror status from the legacy row's own
    (largely vestigial, always-draft-by-default) status field."""
    mirror = _find_logic_mirror(db, ontology_id, rule.id, rule.name_cn)
    expression = {"definition": rule.definition} if rule.definition else {}
    if mirror is None:
        db.add(OntologyLogicRule(
            id=rule.id, ontology_id=ontology_id, name=rule.name_cn,
            logic_type=rule.function_type or _DEFAULT_LOGIC_TYPE,
            description=rule.description, target_entity_type=None,
            expression=expression, enabled=bool(rule.enabled),
            status=rule.status or "draft", version=1,
        ))
        return
    mirror.name = rule.name_cn
    mirror.logic_type = rule.function_type or mirror.logic_type
    mirror.description = rule.description
    mirror.expression = expression
    mirror.enabled = bool(rule.enabled)
    mirror.status = _synced_status(mirror.status, bool(rule.enabled), rule.status or "draft")
    mirror.version = (mirror.version or 0) + 1


def delete_logic_rule_mirror(db: Session, ontology_id: str, rule: LogicRule) -> None:
    mirror = _find_logic_mirror(db, ontology_id, rule.id, rule.name_cn)
    if mirror is not None:
        db.delete(mirror)


def _find_action_mirror(db: Session, ontology_id: str, action_id: str, name: str) -> OntologyActionType | None:
    mirror = db.get(OntologyActionType, action_id)
    if mirror is not None:
        return mirror
    return db.execute(select(OntologyActionType).where(
        OntologyActionType.ontology_id == ontology_id, OntologyActionType.name == name,
    )).scalars().first()


def sync_action_to_v2(db: Session, ontology_id: str, action: Action) -> None:
    """Upsert `action`'s v2 mirror row after a legacy create/update/toggle."""
    mirror = _find_action_mirror(db, ontology_id, action.id, action.name_cn)
    side_effects = {"items": action.side_effects} if action.side_effects else None
    permission_rules = {"rules": action.rules} if action.rules else None
    if mirror is None:
        db.add(OntologyActionType(
            id=action.id, ontology_id=ontology_id, name=action.name_cn,
            description=action.description, target_entity_type=None,
            action_category=_DEFAULT_ACTION_CATEGORY,
            parameters=action.parameters or [],
            submission_criteria={"items": action.submission_criteria} if action.submission_criteria else None,
            effects=action.side_effects or [], side_effects=side_effects,
            permission_rules=permission_rules, enabled=bool(action.enabled),
            status=action.status or "draft", version=1,
        ))
        return
    mirror.name = action.name_cn
    mirror.description = action.description
    mirror.parameters = action.parameters or []
    mirror.submission_criteria = {"items": action.submission_criteria} if action.submission_criteria else None
    mirror.effects = action.side_effects or []
    mirror.side_effects = side_effects
    mirror.permission_rules = permission_rules
    mirror.enabled = bool(action.enabled)
    mirror.status = _synced_status(mirror.status, bool(action.enabled), action.status or "draft")
    mirror.version = (mirror.version or 0) + 1


def delete_action_mirror(db: Session, ontology_id: str, action: Action) -> None:
    mirror = _find_action_mirror(db, ontology_id, action.id, action.name_cn)
    if mirror is not None:
        db.delete(mirror)

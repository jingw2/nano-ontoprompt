"""Ontology working-copy dirty hooks.

`mark_ontology_dirty` is the single dirty transition used by
`OntologyWorkingCopyService.mutate`: it increments `working_revision`, sets
`is_dirty` (only when a release exists), and enqueues the audit outbox in the
caller's transaction.  `ONTOLOGY_DIRTY_HOOKS` is the registry of every
working-schema writer path (see `mutation_inventory.json` for the full
inventory and route closure mapping).
"""
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.services.governance_audit import enqueue_audit

ONTOLOGY_DIRTY_HOOKS = {
    "ontology.update": "ontology.working-copy.ontology.update",
    "entity.create": "ontology.working-copy.entity.create",
    "entity.update": "ontology.working-copy.entity.update",
    "entity.delete": "ontology.working-copy.entity.delete",
    "relation.create": "ontology.working-copy.relation.create",
    "relation.delete": "ontology.working-copy.relation.delete",
    "logic.create": "ontology.working-copy.logic.create",
    "logic.update": "ontology.working-copy.logic.update",
    "logic.delete": "ontology.working-copy.logic.delete",
    "logic.toggle": "ontology.working-copy.logic.toggle",
    "action.create": "ontology.working-copy.action.create",
    "action.update": "ontology.working-copy.action.update",
    "action.delete": "ontology.working-copy.action.delete",
    "action.toggle": "ontology.working-copy.action.toggle",
    "v2logic.create": "ontology.working-copy.v2logic.create",
    "v2logic.update": "ontology.working-copy.v2logic.update",
    "v2logic.review": "ontology.working-copy.v2logic.review",
    "v2logic.delete": "ontology.working-copy.v2logic.delete",
    "v2logic.discover": "ontology.working-copy.v2logic.discover",
    "v2action.create": "ontology.working-copy.v2action.create",
    "v2action.review": "ontology.working-copy.v2action.review",
    "v2action.delete": "ontology.working-copy.v2action.delete",
    "v2action.discover": "ontology.working-copy.v2action.discover",
}


def mark_ontology_dirty(db: Session, *, ontology_id: str, actor_id: str, operation: str,
                        security_domain_id: str | None = None, dirty: bool = True) -> None:
    if security_domain_id is None:
        security_domain_id = db.execute(
            sa.text("SELECT security_domain_id FROM ontology_projects WHERE id = :id"),
            {"id": ontology_id},
        ).scalar_one()
    db.execute(
        sa.text(
            "UPDATE ontology_projects SET working_revision = working_revision + 1, "
            "is_dirty = :dirty, updated_at = CURRENT_TIMESTAMP WHERE id = :id"
        ),
        {"dirty": dirty, "id": ontology_id},
    )
    enqueue_audit(
        db.connection(),
        security_domain_id=security_domain_id,
        correlation_id=f"wc:{ontology_id}:{operation}:{uuid.uuid4().hex[:8]}",
        operation=ONTOLOGY_DIRTY_HOOKS.get(operation, f"ontology.working-copy.{operation}"),
        decision="allow",
        outcome="succeeded",
        actor_user_id=actor_id,
        retention_class="standard",
    )

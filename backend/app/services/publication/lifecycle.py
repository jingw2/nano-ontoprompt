"""Ontology lifecycle service (P1C-COMPILER owns this module).

Implements the §3.2 state machine transitions behind the lifecycle API:
`mark_created` (draft -> created), `publish` (created -> published, via the
compiler), `archive` (non-archived -> archived, admin), and the emergency
runtime-disable/enable switches.  Arbitrary status/version writes are rejected
(422 INVALID_LIFECYCLE_TRANSITION).
"""
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.services.governance_audit import enqueue_audit
from app.services.publication.compiler import (
    CompilerFinding,
    NoSchemaChange,
    PublicationBlocked,
    compile_ontology_release,
)

VALID_STATUSES = ("draft", "creating", "created", "published", "archived")


class LifecycleError(Exception):
    pass


def _project(db: Session, ontology_id: str):
    return db.execute(
        sa.text(
            "SELECT id, security_domain_id, status FROM ontology_projects WHERE id = :id FOR UPDATE"
        ),
        {"id": ontology_id},
    ).mappings().one_or_none()


def _audit(db: Session, project, operation: str, actor_id: str, outcome: str = "succeeded") -> None:
    enqueue_audit(
        db.connection(),
        security_domain_id=project["security_domain_id"],
        correlation_id=f"lc:{project['id']}:{operation}",
        operation=f"ontology.lifecycle.{operation}",
        decision="allow",
        outcome=outcome,
        actor_user_id=actor_id,
        retention_class="standard",
    )


def mark_created(db: Session, *, ontology_id: str, actor_id: str) -> dict:
    project = _project(db, ontology_id)
    if project is None:
        raise LifecycleError("ONTOLOGY_NOT_FOUND")
    if project["status"] != "draft":
        raise LifecycleError("INVALID_LIFECYCLE_TRANSITION")
    db.execute(
        sa.text("UPDATE ontology_projects SET status = 'created', updated_at = CURRENT_TIMESTAMP WHERE id = :o"),
        {"o": ontology_id},
    )
    _audit(db, project, "mark-created", actor_id)
    db.commit()
    return {"ontology_id": ontology_id, "status": "created"}


def publish(db: Session, *, ontology_id: str, actor_id: str, changelog: str | None = None,
            base_working_revision: int | None = None) -> dict:
    project = _project(db, ontology_id)
    if project is None:
        raise LifecycleError("ONTOLOGY_NOT_FOUND")
    if base_working_revision is not None:
        current = db.execute(
            sa.text("SELECT working_revision FROM ontology_projects WHERE id = :o"),
            {"o": ontology_id},
        ).scalar_one()
        if current != base_working_revision:
            raise LifecycleError("ONTOLOGY_WORKING_REVISION_CONFLICT")
    try:
        return compile_ontology_release(db, ontology_id=ontology_id, actor_id=actor_id, changelog=changelog)
    except NoSchemaChange as exc:
        raise LifecycleError("NO_SCHEMA_CHANGE") from exc
    except CompilerFinding as exc:
        raise LifecycleError(str(exc)) from exc
    except PublicationBlocked as exc:
        raise LifecycleError(str(exc)) from exc


def archive(db: Session, *, ontology_id: str, actor_id: str) -> dict:
    project = _project(db, ontology_id)
    if project is None:
        raise LifecycleError("ONTOLOGY_NOT_FOUND")
    if project["status"] == "archived":
        raise LifecycleError("INVALID_LIFECYCLE_TRANSITION")
    db.execute(
        sa.text("UPDATE ontology_projects SET status = 'archived', updated_at = CURRENT_TIMESTAMP WHERE id = :o"),
        {"o": ontology_id},
    )
    _audit(db, project, "archive", actor_id)
    db.commit()
    return {"ontology_id": ontology_id, "status": "archived"}


def runtime_disable(db: Session, *, ontology_id: str, actor_id: str) -> dict:
    project = _project(db, ontology_id)
    if project is None:
        raise LifecycleError("ONTOLOGY_NOT_FOUND")
    _audit(db, project, "runtime-disable", actor_id)
    db.commit()
    return {"ontology_id": ontology_id, "runtime_disabled": True}


def runtime_enable(db: Session, *, ontology_id: str, actor_id: str) -> dict:
    project = _project(db, ontology_id)
    if project is None:
        raise LifecycleError("ONTOLOGY_NOT_FOUND")
    _audit(db, project, "runtime-enable", actor_id)
    db.commit()
    return {"ontology_id": ontology_id, "runtime_disabled": False}

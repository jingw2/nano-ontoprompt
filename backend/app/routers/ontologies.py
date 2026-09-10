from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from typing import Optional
from app.deps import get_db, get_current_user, require_admin, require_editor
from app.models.ontology import OntologyProject
from app.models.entity import Entity
from app.models.relation import Relation
from app.models.user import User
from app.schemas.ontology import OntologyCreate, OntologyOut, OntologyListItem, OntologyUpdate
from app.services.ontology_access import creator_grant
from app.services.publication.working_copy import OntologyWorkingCopyService
import uuid

router = APIRouter()

@router.get("")
def list_ontologies(
    name: Optional[str] = None,
    page: int = 1, page_size: int = 20,
    db: Session = Depends(get_db), _=Depends(get_current_user)
):
    q = db.query(OntologyProject)
    if name:
        q = q.filter(OntologyProject.name.ilike(f"%{name}%"))
    total = q.count()
    items = q.order_by(OntologyProject.updated_at.desc()).offset((page-1)*page_size).limit(page_size).all()
    result = []
    for item in items:
        d = OntologyListItem.model_validate(item).model_dump()
        d['entity_count'] = db.query(func.count(Entity.id)).filter(Entity.ontology_id == item.id).scalar() or 0
        d['relation_count'] = db.query(func.count(Relation.id)).filter(Relation.ontology_id == item.id).scalar() or 0
        result.append(d)
    return {"data": {"items": result, "total": total, "page": page, "page_size": page_size}}

@router.post("", status_code=201)
def create_ontology(body: OntologyCreate, db: Session = Depends(get_db), current_user: User = Depends(require_editor)):
    existing = db.query(OntologyProject).filter(OntologyProject.name.ilike(body.name)).first()
    if existing:
        raise HTTPException(status_code=409, detail={"error": "DUPLICATE_NAME", "message": f"Ontology 名称「{body.name}」已存在", "existing_id": existing.id})
    project = OntologyProject(id=str(uuid.uuid4()), name=body.name, domain=body.domain,
                               description=body.description, build_mode=body.build_mode or "simple_llm",
                               created_by=current_user.id)
    db.add(project); db.flush()
    # atomically insert the exact creator grant (P1A-ACCESS contract)
    creator_grant(db, project.id, current_user)
    db.refresh(project)
    return {"data": OntologyOut.model_validate(project).model_dump()}

@router.get("/{ontology_id}")
def get_ontology(ontology_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    p = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if not p:
        raise HTTPException(404, "Not found")
    return {"data": OntologyOut.model_validate(p).model_dump()}

@router.put("/{ontology_id}")
def update_ontology(ontology_id: str, body: OntologyUpdate, db: Session = Depends(get_db), current_user: User = Depends(require_editor)):
    p = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if not p:
        raise HTTPException(404, "Not found")
    def _write():
        for k, v in body.model_dump(exclude_none=True).items():
            setattr(p, k, v)
        db.flush()
        return {"data": OntologyOut.model_validate(p).model_dump()}
    return OntologyWorkingCopyService.mutate(db, ontology_id=ontology_id, actor_id=current_user.id, operation="ontology.update", callback=_write)

@router.delete("/{ontology_id}", status_code=204)
def delete_ontology(ontology_id: str, db: Session = Depends(get_db), _=Depends(require_admin)):
    p = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if not p:
        raise HTTPException(404, "Not found")
    bound_agent = db.execute(text(
        "SELECT 1 FROM agent_ontology_bindings WHERE ontology_id = :id LIMIT 1"
    ), {"id": ontology_id}).scalar_one_or_none()
    if bound_agent:
        raise HTTPException(409, detail="ONTOLOGY_BOUND_TO_AGENT")
    # Access-control/review-annotation/schema-metadata/outbox rows only make
    # sense while the ontology exists; they carry no audit value once it's
    # gone, unlike execution history (agent runs, sandbox simulations,
    # business-journey records, MCP write requests) rooted through
    # ontology_releases, which a delete must not silently wipe.
    for table in (
        "ontology_data_grants", "ontology_project_access_grants", "ontology_migration_findings",
        "agent_index_outbox", "entity_property_definitions", "entity_instance_relations",
    ):
        db.execute(text(f"DELETE FROM {table} WHERE ontology_id = :id"), {"id": ontology_id})
    try:
        db.delete(p)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, detail="ONTOLOGY_HAS_DEPENDENT_DATA")

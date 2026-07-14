from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional
from app.deps import get_db, get_current_user
from app.models.ontology import OntologyProject
from app.models.entity import Entity
from app.models.relation import Relation
from app.models.user import User
from app.schemas.ontology import OntologyCreate, OntologyOut, OntologyListItem, OntologyUpdate
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
def create_ontology(body: OntologyCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    existing = db.query(OntologyProject).filter(OntologyProject.name.ilike(body.name)).first()
    if existing:
        raise HTTPException(status_code=409, detail={"error": "DUPLICATE_NAME", "message": f"Ontology 名称「{body.name}」已存在", "existing_id": existing.id})
    project = OntologyProject(id=str(uuid.uuid4()), name=body.name, domain=body.domain,
                               description=body.description, build_mode=body.build_mode or "simple_llm",
                               created_by=current_user.id)
    db.add(project); db.commit(); db.refresh(project)
    return {"data": OntologyOut.model_validate(project).model_dump()}

@router.get("/{ontology_id}")
def get_ontology(ontology_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    p = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if not p:
        raise HTTPException(404, "Not found")
    return {"data": OntologyOut.model_validate(p).model_dump()}

@router.put("/{ontology_id}")
def update_ontology(ontology_id: str, body: OntologyUpdate, db: Session = Depends(get_db), _=Depends(get_current_user)):
    p = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if not p:
        raise HTTPException(404, "Not found")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(p, k, v)
    db.commit(); db.refresh(p)
    return {"data": OntologyOut.model_validate(p).model_dump()}

@router.delete("/{ontology_id}", status_code=204)
def delete_ontology(ontology_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    p = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if not p:
        raise HTTPException(404, "Not found")
    db.delete(p); db.commit()

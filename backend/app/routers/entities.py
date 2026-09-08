from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.deps import get_db, get_current_user, require_admin, require_editor
from app.models.entity import Entity
from app.models.user import User
from app.services.publication.working_copy import OntologyWorkingCopyService
from app.schemas.entity import EntityCreate, EntityUpdate, EntityOut
import uuid

router = APIRouter()

@router.get("")
def list_entities(ontology_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    items = db.query(Entity).filter(Entity.ontology_id == ontology_id).all()
    return {"data": [EntityOut.model_validate(e).model_dump() for e in items]}

@router.post("", status_code=201)
def create_entity(ontology_id: str, body: EntityCreate, db: Session = Depends(get_db), current_user: User = Depends(require_editor)):
    def _write():
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        e = Entity(id=str(uuid.uuid4()), ontology_id=ontology_id, **data)
        db.add(e); db.flush()
        return {"data": EntityOut.model_validate(e).model_dump()}
    return OntologyWorkingCopyService.mutate(db, ontology_id=ontology_id, actor_id=current_user.id, operation="entity.create", callback=_write)

@router.get("/{entity_id}")
def get_entity(ontology_id: str, entity_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    e = db.query(Entity).filter(Entity.id == entity_id, Entity.ontology_id == ontology_id).first()
    if not e:
        raise HTTPException(404, "Not found")
    return {"data": EntityOut.model_validate(e).model_dump()}

@router.put("/{entity_id}")
def update_entity(ontology_id: str, entity_id: str, body: EntityUpdate, db: Session = Depends(get_db), current_user: User = Depends(require_editor)):
    e = db.query(Entity).filter(Entity.id == entity_id, Entity.ontology_id == ontology_id).first()
    if not e:
        raise HTTPException(404, "Not found")
    def _write():
        for k, v in body.model_dump(exclude_none=True).items():
            setattr(e, k, v)
        db.flush()
        return {"data": EntityOut.model_validate(e).model_dump()}
    return OntologyWorkingCopyService.mutate(db, ontology_id=ontology_id, actor_id=current_user.id, operation="entity.update", callback=_write)

@router.delete("/{entity_id}", status_code=204)
def delete_entity(ontology_id: str, entity_id: str, db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    e = db.query(Entity).filter(Entity.id == entity_id, Entity.ontology_id == ontology_id).first()
    if not e:
        raise HTTPException(404, "Not found")
    def _write():
        db.delete(e); db.flush()
    return OntologyWorkingCopyService.mutate(db, ontology_id=ontology_id, actor_id=current_user.id, operation="entity.delete", callback=_write)

@router.get("/{entity_id}/instances")
def list_entity_instances(
    ontology_id: str,
    entity_id: str,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    """概念实体下挂的行级实例数据（Pipeline Mapping 与简易 LLM 提取共用同一张表）"""
    from app.models.entity_instance import EntityInstance

    entity = db.query(Entity).filter(
        Entity.id == entity_id, Entity.ontology_id == ontology_id
    ).first()
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")

    instances = db.query(EntityInstance).filter(
        EntityInstance.entity_id == entity_id, EntityInstance.ontology_id == ontology_id
    ).order_by(EntityInstance.created_at).all()

    return {"data": [
        {"id": i.id, "row_identity": i.row_identity, "row_data": i.row_data, "created_at": i.created_at.isoformat()}
        for i in instances
    ]}

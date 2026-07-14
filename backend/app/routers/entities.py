from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.deps import get_db, get_current_user
from app.models.entity import Entity
from app.schemas.entity import EntityCreate, EntityUpdate, EntityOut
import uuid

router = APIRouter()

@router.get("")
def list_entities(ontology_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    items = db.query(Entity).filter(Entity.ontology_id == ontology_id).all()
    return {"data": [EntityOut.model_validate(e).model_dump() for e in items]}

@router.post("", status_code=201)
def create_entity(ontology_id: str, body: EntityCreate, db: Session = Depends(get_db), _=Depends(get_current_user)):
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    e = Entity(id=str(uuid.uuid4()), ontology_id=ontology_id, **data)
    db.add(e); db.commit(); db.refresh(e)
    return {"data": EntityOut.model_validate(e).model_dump()}

@router.get("/{entity_id}")
def get_entity(ontology_id: str, entity_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    e = db.query(Entity).filter(Entity.id == entity_id, Entity.ontology_id == ontology_id).first()
    if not e:
        raise HTTPException(404, "Not found")
    return {"data": EntityOut.model_validate(e).model_dump()}

@router.put("/{entity_id}")
def update_entity(ontology_id: str, entity_id: str, body: EntityUpdate, db: Session = Depends(get_db), _=Depends(get_current_user)):
    e = db.query(Entity).filter(Entity.id == entity_id, Entity.ontology_id == ontology_id).first()
    if not e:
        raise HTTPException(404, "Not found")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(e, k, v)
    db.commit(); db.refresh(e)
    return {"data": EntityOut.model_validate(e).model_dump()}

@router.delete("/{entity_id}", status_code=204)
def delete_entity(ontology_id: str, entity_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    e = db.query(Entity).filter(Entity.id == entity_id, Entity.ontology_id == ontology_id).first()
    if not e:
        raise HTTPException(404, "Not found")
    db.delete(e); db.commit()

@router.get("/{entity_id}/related")
def get_related_for_entity(
    ontology_id: str,
    entity_id: str,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    import json as _json
    from app.models.logic import LogicRule
    from app.models.action import Action

    entity = db.query(Entity).filter(
        Entity.id == entity_id,
        Entity.ontology_id == ontology_id
    ).first()
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")

    name_cn = entity.name_cn

    # Query LogicRules - linked_entities is TEXT storing JSON list (property getter returns list)
    related_logic = []
    for lr in db.query(LogicRule).filter(LogicRule.ontology_id == ontology_id).all():
        try:
            linked = lr.linked_entities or []
            if isinstance(linked, str):
                try:
                    linked = _json.loads(linked)
                except _json.JSONDecodeError:
                    linked = []
        except Exception:
            linked = []
        if isinstance(linked, list) and name_cn in linked:
            related_logic.append({
                "id": lr.id,
                "name_cn": lr.name_cn,
                "name_en": getattr(lr, 'name_en', None),
                "formula": getattr(lr, 'formula', None),
                "confidence": getattr(lr, 'confidence', None),
            })

    # Query Actions - linked_entities is JSON column
    related_actions = []
    for ac in db.query(Action).filter(Action.ontology_id == ontology_id).all():
        linked = ac.linked_entities or []
        if isinstance(linked, str):
            try:
                linked = _json.loads(linked)
            except _json.JSONDecodeError:
                linked = []
        if isinstance(linked, list) and name_cn in linked:
            related_actions.append({
                "id": ac.id,
                "name_cn": ac.name_cn,
                "name_en": getattr(ac, 'name_en', None),
                "description": getattr(ac, 'description', None),
                "confidence": getattr(ac, 'confidence', None),
            })

    return {"logic": related_logic, "actions": related_actions}
